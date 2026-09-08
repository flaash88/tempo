"""Ingestion.

Three ways data gets in, in order of how much Tempo trusts them:

* ``fit_parser`` reads the raw FIT files, which are the recording itself.
* ``intervals_client`` fetches summaries, wellness and the FIT files from
  intervals.icu.
* ``garmin_optional`` adds Body Battery and Training Readiness, and is off
  unless explicitly enabled.

``sync`` orchestrates them and owns the watermark discipline; ``store``
holds the writes and the transaction boundaries.
"""
