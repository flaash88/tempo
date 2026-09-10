/**
 * Screen 5 — Coach.
 *
 * One question at a time against the numbers, with the history the server
 * is willing to carry. The interface keeps the conversation for reading;
 * how much of it travels with the next question is the server's decision,
 * and the budget line is always in view because a spent budget is a state
 * the athlete has to be able to see coming.
 *
 * The composer is pinned below the conversation rather than scrolling
 * with it. With the keyboard open the shell ends at the keys and the tab
 * bar stands down, so the composer is what sits there — and because it is
 * a sibling in normal flow rather than an overlay, the last thing said
 * cannot disappear beneath it.
 */

import { useState } from "react";
import { Card, PrimaryButton, TileHeader } from "../components/Tile";
import { Screen } from "../components/Chrome";
import { useViewport } from "../lib/viewport";
import { AiText } from "./Plan";
import { apiSend, ApiError } from "../lib/api";
import { useResource } from "../lib/useResource";
import type { AiAnswer, AiBudget } from "../lib/types";
import { formatEuro } from "../lib/format";

type Turn = { role: "user" | "assistant"; text: string };

export default function Coach() {
  const { keyboardOpen } = useViewport();
  const budget = useResource<AiBudget>("/api/ai/budget");
  const [history, setHistory] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AiAnswer | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);

  const exhausted = budget.data?.exhausted ?? false;

  async function ask() {
    const asked = question.trim();
    if (!asked) return;
    setThinking(true);
    setProblem(null);
    try {
      const result = await apiSend<AiAnswer>("/api/ai/chat", {
        body: { question: asked, history },
      });
      setAnswer(result);
      setHistory((previous) => [
        ...previous,
        { role: "user", text: asked },
        { role: "assistant", text: result.text },
      ]);
      setQuestion("");
      budget.reload();
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : "Die Frage kam nicht durch.");
    } finally {
      setThinking(false);
    }
  }

  const composer = (
    <div
      className="flex shrink-0 flex-col gap-2 px-4 pt-3"
      style={{
        // No safe-area padding while the keyboard is up: the home
        // indicator is behind the keys, and the tab bar carries the inset
        // the rest of the time.
        paddingBottom: keyboardOpen ? "var(--sp-3)" : "var(--sp-4)",
        paddingLeft: "calc(var(--inset-left) + var(--sp-4))",
        paddingRight: "calc(var(--inset-right) + var(--sp-4))",
        background: "var(--t-bg)",
        boxShadow: "inset 0 1px 0 0 var(--t-line)",
      }}
    >
      <label className="sr-only" htmlFor="coach-question">
        Frage an den Coach
      </label>
      <textarea
        id="coach-question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        rows={keyboardOpen ? 2 : 3}
        placeholder="Was sagen die Zahlen zur letzten Woche?"
        className="w-full p-3 text-body"
        style={{
          borderRadius: "var(--r-md)",
          background: "var(--t-surface-2)",
          color: "var(--t-ink)",
          border: "none",
          resize: "none",
        }}
      />
      {problem ? (
        <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
          {problem}
        </p>
      ) : null}
      <PrimaryButton onClick={ask} disabled={thinking || exhausted || !question.trim()}>
        {thinking ? "Denkt nach …" : "Fragen"}
      </PrimaryButton>
    </div>
  );

  return (
    <Screen
      title="Coach"
      subtitle={
        budget.data
          ? `${formatEuro(budget.data.remaining_eur)} von ${formatEuro(budget.data.budget_eur)} übrig`
          : undefined
      }
      footer={composer}
    >
      {exhausted ? (
        <div
          role="status"
          className="px-3 py-2"
          style={{
            borderRadius: "var(--r-md)",
            background: "var(--st-caution-soft)",
            boxShadow: "inset 0 0 0 1px var(--st-caution)",
          }}
        >
          <p className="m-0 text-sub" style={{ color: "var(--st-caution)" }}>
            Das Monatsbudget ist aufgebraucht. Im {budget.data?.month} wird nichts mehr
            ausgewertet.
          </p>
        </div>
      ) : null}

      {history.length === 0 && !answer ? (
        // The composer moved out of the scrolling area, so without this
        // the screen is blank until the first question. It says what the
        // Coach can and cannot do, which is the useful thing to read
        // while deciding what to ask.
        <Card>
          <TileHeader title="Was hier möglich ist" />
          <p className="m-0 mt-3 text-sub" style={{ color: "var(--t-ink-2)" }}>
            Gefragt wird gegen die eigenen Zahlen. Was die Daten nicht
            hergeben, wird nicht beantwortet — dann steht dort, was dafür
            fehlt. Jede Antwort kostet etwas vom Monatsbudget.
          </p>
        </Card>
      ) : null}

      {history.length > 0 ? (
        <Card>
          <TileHeader title="Verlauf" />
          <div className="mt-3 flex flex-col gap-3">
            {history.map((turn, index) => (
              <div key={index} className="flex flex-col">
                <span className="label-micro">
                  {turn.role === "user" ? "Frage" : "Antwort"}
                </span>
                <span
                  className="text-sub"
                  style={{ color: turn.role === "user" ? "var(--t-ink-2)" : "var(--t-ink)" }}
                >
                  {turn.text}
                </span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {answer ? (
        <Card>
          <TileHeader title="Antwort" />
          <div className="mt-3">
            <AiText answer={answer} />
          </div>
        </Card>
      ) : null}

    </Screen>
  );
}
