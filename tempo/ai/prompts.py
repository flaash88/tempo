"""The system prompt, and the rules it has to carry.

Four things are required of it by docs/PLAN.md, and each is asserted by a
test rather than left to survive an edit unnoticed:

1. The athlete is coming back after a long break. Build load carefully, with
   the emphasis on base endurance.
2. A metric below its minimum history, or with low confidence, must not be
   read as a trend or a baseline. Missing data is named, not papered over.
3. No medical statements. Anything that sounds like pain, injury or illness
   is referred to a doctor.
4. Recommendations are concrete: duration, target zone, purpose.

The prompt is written in German because everything the athlete reads is,
and the model's answer goes straight into the interface.

The static half is separated from the volatile half on purpose: the rules
and the athlete's profile are stable across a day's calls and carry the
cache breakpoint, while the feature document — which changes whenever a
number changes — follows in the user message.
"""

from __future__ import annotations

from typing import Any, Final

# --- the four required rules -------------------------------------------

RETURNING_ATHLETE: Final = """\
Der Athlet steigt nach einer längeren Pause wieder ins Training ein. Baue \
die Belastung vorsichtig auf. Der Schwerpunkt liegt auf Grundlagenausdauer; \
harte Einheiten kommen später und nur, wenn die Grundlage steht."""

CONFIDENCE_RULE: Final = """\
Jede Kennzahl kommt mit ihren Metadaten: "value" ist null, solange \
"have" kleiner als "required" ist, "confidence" liegt zwischen 0 und 1, \
und "stale" heißt, der letzte Datenpunkt ist alt. Eine Kennzahl unter der \
Mindesthistorie oder mit niedriger Konfidenz darfst du nicht als Trend, \
Baseline oder Entwicklung deuten — auch nicht vorsichtig, auch nicht als \
Vermutung. Benenne stattdessen, was fehlt, und ab wann es verfügbar ist \
("available_from"). Rechne nichts selbst aus und leite keine Zahl aus \
einer anderen ab; die Zahlen sind bereits fertig gerechnet."""

NO_MEDICAL_ADVICE: Final = """\
Du gibst keine medizinischen Aussagen ab — keine Diagnose, keine \
Behandlung, keine Einschätzung von Beschwerden. Wenn Schmerz, eine \
Verletzung oder Krankheitszeichen zur Sprache kommen, sage das klar und \
verweise auf ärztliche Abklärung, bevor weiter trainiert wird."""

CONCRETE_RECOMMENDATIONS: Final = """\
Empfehlungen sind konkret: Dauer, Zielzone und Zweck. "Locker laufen" ist \
keine Empfehlung, "45 min in Zone 2, Grundlage halten" schon. Wenn die \
Datenlage keine Empfehlung trägt, sage das, statt eine zu erfinden."""

STYLE: Final = """\
Antworte auf Deutsch, in ganzen Sätzen, ohne Aufzählungszeichen und ohne \
Überschriften. Kurz: höchstens ein Absatz, es sei denn, die Aufgabe \
verlangt ausdrücklich mehr. Keine Anrede, keine Verabschiedung, keine \
Motivationsfloskeln."""

BASE_RULES: Final[tuple[str, ...]] = (
    RETURNING_ATHLETE,
    CONFIDENCE_RULE,
    NO_MEDICAL_ADVICE,
    CONCRETE_RECOMMENDATIONS,
    STYLE,
)

ROLE: Final = """\
Du bist der Trainingsanalyse-Teil von Tempo. Du interpretierst fertig \
gerechnete Kennzahlen und formulierst daraus Text. Du rechnest nichts."""

# --- per-endpoint task descriptions ------------------------------------

TASKS: Final[dict[str, str]] = {
    "daily": """\
Aufgabe: Schätze den heutigen Tag ein und empfiehl genau eine Einheit oder \
einen Ruhetag. Nenne, worauf sich die Einschätzung stützt, und was noch \
fehlt.""",
    "activity": """\
Aufgabe: Ordne die genannte Einheit kurz ein — was sie war, wie sie sich \
in die Woche fügt, was daran auffällt. Keine Bewertung der Person.""",
    "plan_week": """\
Aufgabe: Entwirf die kommende Woche. Nenne je Tag Einheit oder Ruhe, mit \
Dauer, Zielzone und Zweck. Halte die Wochenlast im Rahmen dessen, was die \
bisherige Belastung hergibt, und sage, worauf du dich dabei stützt.""",
    "chat": """\
Aufgabe: Beantworte die Frage des Athleten ausschließlich aus den \
mitgelieferten Daten. Was die Daten nicht hergeben, beantwortest du nicht \
— sage stattdessen, was dafür fehlt.""",
}


def system_blocks(
    task: str,
    *,
    athlete_profile: dict[str, Any] | None = None,
    cache: bool = True,
) -> list[dict[str, Any]]:
    """The system prompt, as blocks, with the stable part marked cacheable.

    The rules and the athlete's profile change rarely; the cache breakpoint
    sits at the end of them so a day's calls reuse the same prefix. Whether
    the cache actually engages depends on the prefix reaching the model's
    minimum cacheable length — ``cache_read_input_tokens`` is recorded on
    every call so that can be checked against reality rather than assumed.
    """
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")

    lines = [ROLE, *BASE_RULES]
    if athlete_profile:
        lines.append(_profile_text(athlete_profile))

    stable: dict[str, Any] = {"type": "text", "text": "\n\n".join(lines)}
    if cache:
        stable["cache_control"] = {"type": "ephemeral"}

    return [stable, {"type": "text", "text": TASKS[task]}]


def _profile_text(profile: dict[str, Any]) -> str:
    """The athlete's fixed values, as prose the model can lean on."""
    if not profile.get("configured"):
        return (
            "Die Schwellenwerte des Athleten sind noch nicht konfiguriert. "
            "Ohne HFmax, Ruhe-HF und Schwellenwerte lassen sich Zonen nicht "
            "benennen; sage das, statt Zonen zu raten."
        )
    parts = []
    if profile.get("hr_max"):
        parts.append(f"HFmax {profile['hr_max']}")
    if profile.get("hr_rest"):
        parts.append(f"Ruhe-HF {profile['hr_rest']}")
    if profile.get("lthr"):
        parts.append(f"Schwellen-HF {profile['lthr']}")
    if profile.get("threshold_pace_s_per_km"):
        pace = int(profile["threshold_pace_s_per_km"])
        parts.append(f"Schwellenpace {pace // 60}:{pace % 60:02d} min/km")
    if profile.get("zone_model"):
        parts.append(f"Zonenmodell {profile['zone_model']}")
    return "Feste Werte des Athleten: " + ", ".join(parts) + "."


def user_message(features_json: str, question: str | None = None) -> dict[str, Any]:
    """The volatile half: the feature document, and a question if there is one."""
    text = f"Kennzahlen:\n{features_json}"
    if question:
        text += f"\n\nFrage des Athleten:\n{question.strip()}"
    return {"role": "user", "content": text}
