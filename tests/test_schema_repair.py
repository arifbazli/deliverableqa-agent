import pytest

from agents.schema import AgentFindings, _inline_refs, _parse_leading_json, _repair_tool_input


class TestParseLeadingJson:
    def test_parses_clean_json(self):
        assert _parse_leading_json('{"a": 1}') == {"a": 1}

    def test_parses_json_with_trailing_garbage(self):
        # The exact failure mode found in live Bedrock testing: a valid JSON array
        # followed by a single stray "}" that belongs to an outer object the model
        # never wrapped it in.
        text = '[{"id": "x"}]}'
        assert _parse_leading_json(text) == [{"id": "x"}]

    def test_raises_on_genuinely_invalid_json(self):
        with pytest.raises(Exception):
            _parse_leading_json("not json at all")


class TestRepairToolInput:
    def test_correctly_shaped_input_passes_through(self):
        raw = {"agent": "consistency", "findings": []}
        assert _repair_tool_input(raw) == raw

    def test_whole_payload_nested_as_string_in_findings_field(self):
        # Shape (a) from the docstring: {"findings": "{\"agent\": ..., \"findings\": [...]}"}
        raw = {"findings": '{"agent": "consistency", "findings": [{"id": "c1"}]}'}

        repaired = _repair_tool_input(raw)

        assert repaired == {"agent": "consistency", "findings": [{"id": "c1"}]}

    def test_findings_field_stringified_only(self):
        # Shape (b): {"agent": "...", "findings": "[{...}, ...]"}
        raw = {"agent": "consistency", "findings": '[{"id": "c1"}, {"id": "c2"}]'}

        repaired = _repair_tool_input(raw)

        assert repaired == {"agent": "consistency", "findings": [{"id": "c1"}, {"id": "c2"}]}

    def test_findings_field_stringified_with_trailing_garbage(self):
        # The combined real-world case: findings stringified AND trailing garbage.
        raw = {"agent": "consistency", "findings": '[{"id": "c1"}]}'}

        repaired = _repair_tool_input(raw)

        assert repaired == {"agent": "consistency", "findings": [{"id": "c1"}]}

    def test_unparseable_findings_string_left_as_is(self):
        # If it truly can't be parsed, don't crash — let AgentFindings.model_validate
        # raise the real validation error downstream.
        raw = {"agent": "consistency", "findings": "not json"}

        repaired = _repair_tool_input(raw)

        assert repaired == raw

    def test_full_pipeline_recovers_real_findings(self):
        raw = {"findings": '{"agent": "structure", "findings": [{"id": "s1", "location": {"page": null, "section": "Risks"}, "severity": "critical", "category": "missing_section", "description": "d", "evidence": "e", "proposed_fix": "f"}]}}'}

        result = AgentFindings.model_validate(_repair_tool_input(raw))

        assert result.agent == "structure"
        assert len(result.findings) == 1
        assert result.findings[0].id == "s1"


class TestInlineRefs:
    def test_removes_all_refs_and_defs(self):
        schema = AgentFindings.model_json_schema()

        flat = _inline_refs(schema)

        assert "$defs" not in flat
        assert "$ref" not in str(flat)

    def test_flattened_schema_still_describes_the_same_shape(self):
        flat = _inline_refs(AgentFindings.model_json_schema())

        assert flat["properties"]["agent"]["type"] == "string"
        assert flat["properties"]["findings"]["type"] == "array"
        finding_schema = flat["properties"]["findings"]["items"]
        assert set(finding_schema["required"]) == {
            "id", "location", "severity", "category", "description", "evidence", "proposed_fix",
        }
