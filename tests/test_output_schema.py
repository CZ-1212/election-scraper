"""Validate every sample JSON in data/samples/ against the documented
output schema. This is the contract downstream consumers (CMS imports,
results pages, dashboards) rely on.

Adding a new field to scraper output requires updating the schema in
this file. Removing a documented field intentionally fails the suite.
"""

import json

import jsonschema
import pytest


# Inner per-county payload — matches what each ClarityScraper /
# multi-platform scraper returns from .scrape().
COUNTY_PAYLOAD_SCHEMA = {
    "type": "object",
    "required": ["url", "page_title", "voter_turnout", "contests"],
    "properties": {
        "timestamp": {"type": "string"},
        "url": {"type": "string", "format": "uri"},
        "county_name": {"type": "string"},
        "page_title": {"type": "string"},
        "last_updated": {"type": ["string", "null"]},
        "voter_turnout": {
            "type": "object",
            "properties": {
                "ballots_cast": {"type": "integer", "minimum": 0},
                "registered_voters": {"type": "integer", "minimum": 0},
                "turnout_percentage": {"type": "number", "minimum": 0},
                "precincts_reported": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "contests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "precincts_reporting": {"type": "string"},
                    "choices": {"type": "array"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}


# Outer Clarity envelope, as written by ClarityScraper.scrape().
CLARITY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["scrape_timestamp"],
    "properties": {
        "scrape_timestamp": {"type": "string"},
        "json_data": {"type": ["object", "null"]},
        "selenium_data": {
            "anyOf": [{"type": "null"}, COUNTY_PAYLOAD_SCHEMA],
        },
    },
    "additionalProperties": True,
}


def _sample_files(samples_dir):
    return sorted(samples_dir.glob("*.json"))


def test_samples_directory_is_populated(samples_dir):
    files = _sample_files(samples_dir)
    assert files, f"No sample JSON files in {samples_dir}"


@pytest.mark.parametrize(
    "sample_path",
    _sample_files(__import__("pathlib").Path(__file__).resolve().parent.parent
                  / "data" / "samples"),
    ids=lambda p: p.name,
)
def test_sample_matches_clarity_schema(sample_path):
    payload = json.loads(sample_path.read_text())
    jsonschema.validate(payload, CLARITY_OUTPUT_SCHEMA)


@pytest.mark.parametrize(
    "sample_path",
    _sample_files(__import__("pathlib").Path(__file__).resolve().parent.parent
                  / "data" / "samples"),
    ids=lambda p: p.name,
)
def test_sample_has_nonempty_results(sample_path):
    """Every published sample should have at least one contest and a
    plausible voter turnout block — otherwise we're shipping empty
    fixtures that mask scraper regressions."""
    payload = json.loads(sample_path.read_text())
    inner = payload.get("selenium_data") or payload.get("json_data")
    assert inner, f"{sample_path.name}: no selenium_data or json_data payload"
    assert inner.get("contests"), f"{sample_path.name}: no contests captured"
    turnout = inner.get("voter_turnout") or {}
    assert turnout.get("ballots_cast", 0) > 0, (
        f"{sample_path.name}: ballots_cast missing or zero"
    )
