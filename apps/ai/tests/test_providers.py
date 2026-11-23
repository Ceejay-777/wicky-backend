from unittest.mock import MagicMock, patch

from apps.ai.providers import PROVIDERS, build_groq_response_format


class TestProviderConfig:
    def test_providers_dict_has_expected_keys(self):
        assert "groq" in PROVIDERS
        assert "gemini" in PROVIDERS
        assert "opencode_zen" not in PROVIDERS


class TestResponseFormatBuilders:
    def test_groq_format_has_best_effort_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = build_groq_response_format(schema, "TestSchema")
        assert result["type"] == "json_schema"
        assert result["json_schema"]["strict"] is False
        assert result["json_schema"]["name"] == "TestSchema"
        assert result["json_schema"]["schema"] == schema


class TestMessageBuilders:
    def test_build_generation_messages_with_schema(self):
        from apps.ai.prompts import build_generation_messages
        messages = build_generation_messages(
            prompt="Generate content",
            system_instruction="Be educational",
            output_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "Be educational" in messages[0]["content"]
        assert "Respond with valid JSON" in messages[1]["content"]

    def test_build_resolve_messages(self):
        from apps.ai.prompts import build_resolve_messages
        messages = build_resolve_messages(
            input_text="machine learning",
            candidates=[{"id": 1, "score": 0.9}],
        )
        assert len(messages) == 2
        assert "machine learning" in messages[1]["content"]


class TestSanitizeSchemaForGemini:
    UNSUPPORTED = {"minItems", "maxItems", "minimum", "maximum", "additionalProperties"}

    @staticmethod
    def _keys(node):
        found = set()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "properties":
                    for sub in v.values():
                        found |= TestSanitizeSchemaForGemini._keys(sub)
                else:
                    found.add(k)
                    found |= TestSanitizeSchemaForGemini._keys(v)
        elif isinstance(node, list):
            for v in node:
                found |= TestSanitizeSchemaForGemini._keys(v)
        return found

    def test_strips_unsupported_keywords_recursively(self):
        from apps.ai.providers import sanitize_schema_for_gemini
        schema = {
            "type": "object",
            "properties": {
                "xs": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 3}},
                        "required": ["n"],
                    },
                }
            },
            "required": ["xs"],
        }
        out = sanitize_schema_for_gemini(schema)
        assert not (self._keys(out) & self.UNSUPPORTED)
        assert out["properties"]["xs"]["items"]["properties"]["n"] == {"type": "integer"}
        assert out["required"] == ["xs"]

    def test_does_not_mutate_input(self):
        from apps.ai.providers import sanitize_schema_for_gemini
        schema = {"type": "array", "minItems": 1, "items": {"type": "string"}}
        sanitize_schema_for_gemini(schema)
        assert schema["minItems"] == 1

    def test_keeps_property_named_like_a_keyword(self):
        from apps.ai.providers import sanitize_schema_for_gemini
        schema = {"type": "object", "properties": {"minimum": {"type": "integer"}}}
        assert "minimum" in sanitize_schema_for_gemini(schema)["properties"]

    def test_call_gemini_native_sends_sanitized_schema(self):
        from apps.ai.providers import call_gemini_native
        schema = {"type": "array", "minItems": 1, "items": {"type": "string"}}
        fake_response = MagicMock(text="[]")
        with patch("google.generativeai.configure"), patch(
            "google.generativeai.GenerationConfig"
        ) as gen_cfg, patch("google.generativeai.GenerativeModel") as model_cls, patch.dict(
            "os.environ", {"GEMINI_API_KEY": "k"}
        ):
            model_cls.return_value.generate_content.return_value = fake_response
            call_gemini_native([{"role": "user", "content": "hi"}], schema)
        assert "minItems" not in gen_cfg.call_args.kwargs["response_schema"]

    def test_call_gemini_native_omits_empty_system_instruction(self):
        from apps.ai.providers import call_gemini_native
        with patch("google.generativeai.configure"), patch(
            "google.generativeai.GenerationConfig"
        ), patch("google.generativeai.GenerativeModel") as model_cls, patch.dict(
            "os.environ", {"GEMINI_API_KEY": "k"}
        ):
            model_cls.return_value.generate_content.return_value = MagicMock(text="{}")
            call_gemini_native([{"role": "user", "content": "hi"}], {"type": "object"})
        assert model_cls.call_args.kwargs["system_instruction"] is None
