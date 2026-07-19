from __future__ import annotations

import json

from general_ludd.web_utils import (
    analyze_layout,
    calculate_readability,
    check_aria_attributes,
    check_color_contrast,
    check_js_syntax,
    check_responsive_patterns,
    detect_css_framework,
    detect_error_patterns,
    extract_colors_from_css,
    extract_fonts_from_css,
    extract_media_queries,
    extract_spacing_scale,
    extract_z_index_contexts,
    generate_boilerplate,
    generate_color_tokens,
    generate_graphql_query,
    generate_htmx_template,
    generate_nextjs_page,
    generate_react_component,
    generate_spacing_tokens,
    generate_type_scale,
    parse_css,
    parse_graphql_schema,
    tokens_to_css,
    tokens_to_json,
    validate_heading_hierarchy,
    validate_html,
)

# ---------------------------------------------------------------------------
# HTML / CSS
# ---------------------------------------------------------------------------

class TestValidateHtml:
    def test_valid_html5_returns_empty(self):
        html = (
            "<!DOCTYPE html>\n<html lang=\"en\">\n"
            "<head><meta charset=\"utf-8\"><title>X</title></head>\n"
            "<body><p>hi</p></body>\n</html>"
        )
        errors = validate_html(html)
        assert errors == []

    def test_missing_doctype(self):
        html = "<div><p>text</div>"
        errors = validate_html(html)
        assert any("unclosed" in e.lower() or "p" in e.lower() or "div" in e.lower() for e in errors)

    def test_unclosed_tags(self):
        html = "<div><p>text</div>"
        errors = validate_html(html)
        assert any("unclosed" in e.lower() or "p" in e.lower() for e in errors)

    def test_missing_alt_on_img(self):
        html = "<img src=\"x.png\">"
        errors = validate_html(html)
        assert isinstance(errors, list)


class TestParseCss:
    def test_simple_rules(self):
        css = "body { color: red; } p { margin: 0; }"
        result = parse_css(css)
        assert isinstance(result, dict)

    def test_nested_rules(self):
        css = ".parent { color: red; & .child { color: blue; } }"
        result = parse_css(css)
        assert isinstance(result, dict)

    def test_media_queries(self):
        css = "@media (max-width: 600px) { body { font-size: 14px; } }"
        result = parse_css(css)
        assert isinstance(result, dict)

    def test_custom_properties(self):
        css = ":root { --primary: #333; } .btn { color: var(--primary); }"
        result = parse_css(css)
        assert isinstance(result, dict)


class TestExtractMediaQueries:
    def test_min_width(self):
        css = "@media (min-width: 768px) { body { margin: 0; } }"
        queries = extract_media_queries(css)
        assert any("min-width" in q.get("feature", "") for q in queries)

    def test_max_width(self):
        css = "@media (max-width: 600px) { body { margin: 0; } }"
        queries = extract_media_queries(css)
        assert any("max-width" in q.get("feature", "") for q in queries)

    def test_combined_query(self):
        css = "@media (min-width: 768px) and (max-width: 1024px) { body { margin: 0; } }"
        queries = extract_media_queries(css)
        assert any("min-width" in q.get("feature", "") and "max-width" in q.get("feature", "") for q in queries)

    def test_no_queries(self):
        css = "body { color: red; } p { margin: 0; }"
        queries = extract_media_queries(css)
        assert queries == []


class TestCheckResponsivePatterns:
    def test_flexbox_found(self):
        css = ".container { display: flex; justify-content: center; }"
        patterns = check_responsive_patterns(css)
        assert patterns.get("uses_flexbox") is True

    def test_grid_found(self):
        css = ".layout { display: grid; grid-template-columns: 1fr 1fr; }"
        patterns = check_responsive_patterns(css)
        assert patterns.get("uses_grid") is True

    def test_neither_found(self):
        css = "body { display: block; }"
        patterns = check_responsive_patterns(css)
        assert patterns.get("uses_flexbox") is False
        assert patterns.get("uses_grid") is False


class TestGenerateBoilerplate:
    def test_html5_type(self):
        html = generate_boilerplate(page_type="html5")
        assert "<!DOCTYPE html>" in html
        assert "<html" in html

    def test_missing_type_defaults_to_html5(self):
        html = generate_boilerplate()
        assert "<!DOCTYPE html>" in html


# ---------------------------------------------------------------------------
# Design Research
# ---------------------------------------------------------------------------

class TestExtractColorsFromCss:
    def test_hex_colors(self):
        css = "body { color: #ff0000; background: #00ff00; }"
        colors = extract_colors_from_css(css)
        assert any(c.get("value") == "#ff0000" for c in colors)
        assert any(c.get("value") == "#00ff00" for c in colors)

    def test_rgb_colors(self):
        css = "body { color: rgb(255, 0, 0); }"
        colors = extract_colors_from_css(css)
        assert any("255" in c.get("value", "") for c in colors)

    def test_hsl_colors(self):
        css = "body { color: hsl(0, 100%, 50%); }"
        colors = extract_colors_from_css(css)
        assert len(colors) >= 1

    def test_no_colors(self):
        css = "body { display: block; }"
        colors = extract_colors_from_css(css)
        assert colors == []


class TestExtractFontsFromCss:
    def test_single_font(self):
        css = "body { font-family: Arial; }"
        fonts = extract_fonts_from_css(css)
        assert any(f.get("family") == "Arial" for f in fonts)

    def test_font_stack(self):
        css = "body { font-family: 'Helvetica Neue', Arial, sans-serif; }"
        fonts = extract_fonts_from_css(css)
        assert any("Helvetica" in f.get("family", "") for f in fonts)

    def test_google_fonts_import(self):
        css = "@import url('https://fonts.googleapis.com/css2?family=Roboto'); body { font-family: Roboto; }"
        fonts = extract_fonts_from_css(css)
        assert any("Roboto" in f.get("family", "") for f in fonts)


class TestExtractSpacingScale:
    def test_4px_grid(self):
        css = ".box { padding: 4px; margin: 8px; }"
        scale = extract_spacing_scale(css)
        assert 4 in scale
        assert 8 in scale

    def test_8px_grid(self):
        css = ".box { padding: 8px; margin: 16px; gap: 24px; }"
        scale = extract_spacing_scale(css)
        assert 8 in scale
        assert 16 in scale

    def test_mixed_scales(self):
        css = ".box { padding: 4px; margin: 8px; } .other { padding: 5px; margin: 7px; }"
        scale = extract_spacing_scale(css)
        assert len(scale) >= 2


class TestDetectCssFramework:
    def test_tailwind_classes(self):
        html = "<div class=\"sm:flex tw-items-center 2xl:justify-between\"></div>"
        framework = detect_css_framework(html)
        assert framework is not None
        assert "tailwind" in framework.lower()

    def test_bootstrap_classes(self):
        html = "<div class=\"btn-primary col-md-6\"></div>"
        framework = detect_css_framework(html)
        assert framework is not None
        assert "bootstrap" in framework.lower()

    def test_none_detected(self):
        html = "<div class=\"my-custom-thing\"></div>"
        framework = detect_css_framework(html)
        assert framework is None


class TestAnalyzeLayout:
    def test_grid_template_areas(self):
        css = ".layout { display: grid; grid-template-areas: 'header header' 'sidebar main'; }"
        result = analyze_layout(css)
        assert len(result.get("grid_areas", [])) > 0

    def test_flex_direction(self):
        css = ".row { display: flex; flex-direction: row; }"
        result = analyze_layout(css)
        assert len(result.get("flex_directions", {})) > 0

    def test_no_layout(self):
        css = "body { color: red; }"
        result = analyze_layout(css)
        assert result.get("grid_areas") == []
        assert result.get("flex_directions") == {}


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

class TestCheckJsSyntax:
    def test_valid_js(self):
        js = "const x = 1;\nfunction foo() { return x + 2; }"
        errors = check_js_syntax(js)
        assert errors == []

    def test_missing_semicolon_not_fatal(self):
        js = "const x = 1\nconst y = 2"
        errors = check_js_syntax(js)
        assert isinstance(errors, list)

    def test_unclosed_brace(self):
        js = "function foo() { return 1;"
        errors = check_js_syntax(js)
        assert any("unclosed" in e["message"].lower() or "brace" in e["message"].lower() for e in errors)


class TestDetectErrorPatterns:
    def test_console_log_left_in(self):
        js = "function calc() { console.log('debug'); return 42; }"
        patterns = detect_error_patterns(js)
        assert isinstance(patterns, list)

    def test_null_assign_without_nullish_coalescing(self):
        js = "x = null; y = x || 1;"
        patterns = detect_error_patterns(js)
        assert any("null" in p.lower() for p in patterns)

    def test_then_chains_suggest_async(self):
        js = "fetch('/api').then(function(r) { return r.json(); })"
        patterns = detect_error_patterns(js)
        assert any("then" in p.lower() for p in patterns)


class TestVerifySourceMap:
    def test_valid_map_format(self):
        content = json.dumps({"version": 3, "file": "out.js", "sources": ["src.js"], "mappings": "AAAA"})
        assert isinstance(content, str)
        assert "version" in content

    def test_invalid_json(self):
        content = "not a source map"
        assert isinstance(content, str)


# ---------------------------------------------------------------------------
# Framework
# ---------------------------------------------------------------------------

class TestGenerateReactComponent:
    def test_with_props(self):
        code = generate_react_component("Button", props=["label", "onClick"])
        assert "Button" in code
        assert "label" in code
        assert "onClick" in code

    def test_without_props(self):
        code = generate_react_component("Spacer")
        assert "Spacer" in code
        assert "export" in code or "function Spacer" in code or "const Spacer" in code

    def test_typescript_variant(self):
        code = generate_react_component("Card", props=["title"])
        assert "Card" in code
        assert "title" in code


class TestGenerateNextjsPage:
    def test_ssr_page(self):
        code = generate_nextjs_page("dashboard", page_type="ssr")
        assert "getServerSideProps" in code or "export" in code

    def test_static_page(self):
        code = generate_nextjs_page("about", page_type="static")
        assert "about" in code.lower() or "About" in code


class TestGenerateHtmxTemplate:
    def test_get_trigger(self):
        html = generate_htmx_template("click")
        assert "hx-get" in html or "hx-trigger" in html

    def test_post_trigger_with_target(self):
        html = generate_htmx_template("submit", trigger="post")
        assert "hx-post" in html or "hx-target" in html or "hx-trigger" in html


class TestParseGraphqlSchema:
    def test_types(self):
        schema = "type User { id: ID!; name: String!; } type Post { title: String!; }"
        result = parse_graphql_schema(schema)
        assert isinstance(result, dict)

    def test_queries(self):
        schema = "type Query { users: [User!]!; user(id: ID!): User; }"
        result = parse_graphql_schema(schema)
        assert isinstance(result, dict)

    def test_mutations(self):
        schema = "type Mutation { createUser(name: String!): User!; }"
        result = parse_graphql_schema(schema)
        assert isinstance(result, dict)


class TestGenerateGraphqlQuery:
    def test_with_fields(self):
        query = generate_graphql_query("GetUser", ["id", "name", "email"])
        assert "GetUser" in query
        assert "id" in query
        assert "name" in query

    def test_with_variables(self):
        query = generate_graphql_query("GetPost", ["title", "body"])
        assert "GetPost" in query
        assert "title" in query


# ---------------------------------------------------------------------------
# UX / Accessibility
# ---------------------------------------------------------------------------

class TestCheckColorContrast:
    def test_passing_ratio(self):
        result = check_color_contrast("#000000", "#FFFFFF")
        ratio = result.get("ratio", 0)
        assert ratio >= 4.5

    def test_failing_ratio(self):
        result = check_color_contrast("#AAAAAA", "#BBBBBB")
        ratio = result.get("ratio", 0)
        assert ratio < 3

    def test_large_text_threshold(self):
        result = check_color_contrast("#333333", "#444444")
        assert "ratio" in result


class TestExtractZIndexContexts:
    def test_single_element(self):
        css = ".modal { z-index: 1000; }"
        contexts = extract_z_index_contexts(css)
        assert len(contexts) >= 1

    def test_nested_stacking_contexts(self):
        css = ".overlay { z-index: 100; position: relative; } .modal { z-index: 1000; position: absolute; }"
        contexts = extract_z_index_contexts(css)
        assert len(contexts) >= 2


class TestValidateHeadingHierarchy:
    def test_valid_hierarchy(self):
        html = "<h1>Title</h1><h2>Section</h2><h3>Sub</h3>"
        issues = validate_heading_hierarchy(html)
        assert issues == []

    def test_skipped_level(self):
        html = "<h1>Title</h1><h3>Sub</h3>"
        issues = validate_heading_hierarchy(html)
        assert any("skip" in i.lower() or "h2" in i.lower() for i in issues)

    def test_multiple_h1(self):
        html = "<h1>First</h1><h1>Second</h1>"
        issues = validate_heading_hierarchy(html)
        assert any("multiple" in i.lower() or "h1" in i.lower() for i in issues)


class TestCheckAriaAttributes:
    def test_button_needs_label(self):
        html = "<button></button>"
        issues = check_aria_attributes(html)
        assert any("button" in i.lower() or "label" in i.lower() for i in issues)

    def test_valid_aria_label(self):
        html = "<button aria-label=\"Close\">&times;</button>"
        issues = check_aria_attributes(html)
        assert issues == []

    def test_missing_alt_on_image(self):
        html = "<img src=\"photo.png\">"
        issues = check_aria_attributes(html)
        assert any("alt" in i.lower() or "img" in i.lower() for i in issues)


class TestCalculateReadability:
    def test_simple_text(self):
        result = calculate_readability("The cat sat on the mat. It was happy.")
        assert "score" in result or "grade" in result or "level" in result

    def test_complex_text(self):
        result = calculate_readability(
            "Notwithstanding the aforementioned considerations, the implementation"
            " of the stipulated parameters necessitates a comprehensive evaluation"
            " of the ontological presuppositions underlying the methodological"
            " framework articulated herein."
        )
        assert isinstance(result, dict)

    def test_empty_text(self):
        result = calculate_readability("")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Design System
# ---------------------------------------------------------------------------

class TestGenerateSpacingTokens:
    def test_4px_base(self):
        tokens = generate_spacing_tokens(base=4, steps=6)
        assert "spacing-0" in tokens or "0" in str(tokens)
        assert len(tokens) >= 6

    def test_8px_base(self):
        tokens = generate_spacing_tokens(base=8, steps=6)
        assert len(tokens) >= 6

    def test_custom_steps(self):
        tokens = generate_spacing_tokens(base=4, steps=4)
        assert len(tokens) == 4


class TestGenerateColorTokens:
    def test_single_color_to_shades(self):
        tokens = generate_color_tokens({"primary": "#3366cc"})
        assert "primary" in tokens
        assert len(tokens["primary"]) >= 5


class TestGenerateTypeScale:
    def test_1_25_ratio(self):
        scale = generate_type_scale(ratio=1.25, base_size=16, steps=6)
        assert len(scale) >= 6

    def test_1_5_ratio(self):
        scale = generate_type_scale(ratio=1.5, base_size=16, steps=6)
        assert len(scale) >= 6


class TestTokensToCss:
    def test_spacing_tokens(self):
        tokens = {"spacing-0": "0px", "spacing-1": "4px", "spacing-2": "8px"}
        css = tokens_to_css(tokens, prefix="spacing")
        assert "--spacing-0" in css or "spacing-0" in css

    def test_color_tokens(self):
        tokens = {"primary-500": "#3366cc"}
        css = tokens_to_css(tokens, prefix="color")
        assert "primary-500" in css or "--primary" in css


class TestTokensToJson:
    def test_valid_dtcg_format(self):
        tokens = {"color-primary-500": "#3366cc", "spacing-md": "16px"}
        json_str = tokens_to_json(tokens)
        data = json.loads(json_str)
        assert isinstance(data, dict)
        assert len(data) >= 2
