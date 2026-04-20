# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Baseline repository scaffolding for the public AgenticQueue repo.
- OpenSSF Scorecard workflow and README badge for published score tracking.
- Recorded the first published OpenSSF Scorecard result: `6.1` on 2026-04-19 via `api.scorecard.dev`.
- Golden-corpus retrieval regression coverage for the graph/surface-area/FTS+trgm stack (`tests/retrieval/test_precision.py`, 50 seeded queries; baseline Precision@5 `0.956`, Precision@10 `0.980`).
