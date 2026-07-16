"""Tests for math_typesetting module_utils at collections/.../physics/plugins/module_utils/math_typesetting.py."""

from __future__ import annotations

import os
import sys

import pytest

_COLLECTION_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "collections", "ansible_collections", "general_ludd",
        "physics", "plugins", "module_utils",
    )
)
if _COLLECTION_DIR not in sys.path:
    sys.path.insert(0, _COLLECTION_DIR)

mt = pytest.importorskip("math_typesetting")


class TestValidateLatex:
    def test_valid_simple_expression(self):
        result = mt.validate_latex(r"\frac{1}{2}")
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_invalid_unmatched_open_brace(self):
        result = mt.validate_latex(r"\frac{1}{2{")
        assert result["valid"] is False
        assert any("brace" in e.lower() for e in result["errors"])

    def test_invalid_unmatched_close_brace(self):
        result = mt.validate_latex(r"\frac{1}{2}}")
        assert result["valid"] is False
        assert any("brace" in e.lower() for e in result["errors"])

    def test_invalid_bare_underscore(self):
        result = mt.validate_latex(r"x_y")
        assert result["valid"] is False
        assert any("underscore" in e.lower() for e in result["errors"])

    def test_invalid_bare_caret(self):
        result = mt.validate_latex(r"x^y")
        assert result["valid"] is False
        assert any("caret" in e.lower() for e in result["errors"])

    def test_valid_subscript_in_braces(self):
        result = mt.validate_latex(r"x_{i}")
        assert result["valid"] is True

    def test_valid_superscript_in_braces(self):
        result = mt.validate_latex(r"x^{2}")
        assert result["valid"] is True

    def test_missing_end_for_begin(self):
        result = mt.validate_latex(r"\begin{equation} x=1")
        assert result["valid"] is False
        assert any("end" in e.lower() for e in result["errors"])

    def test_missing_begin_for_end(self):
        result = mt.validate_latex(r"\end{equation} x=1")
        assert result["valid"] is False
        assert any("begin" in e.lower() for e in result["errors"])

    def test_mismatched_begin_end_count(self):
        result = mt.validate_latex(r"\begin{align}\begin{equation}x=1\end{equation}")
        assert result["valid"] is False
        assert any("mismatch" in e.lower() for e in result["errors"])

    def test_double_superscript(self):
        result = mt.validate_latex(r"x^a^b")
        assert result["valid"] is False
        assert any("superscript" in e.lower() for e in result["errors"])

    def test_double_subscript(self):
        result = mt.validate_latex(r"x_a_b")
        assert result["valid"] is False
        assert any("subscript" in e.lower() for e in result["errors"])

    def test_unescaped_ampersand_warning(self):
        result = mt.validate_latex(r"a=b & c=d")
        assert result["valid"] is True
        assert any("ampersand" in w.lower() or "&" in w for w in result["warnings"])

    def test_fully_valid_expression(self):
        result = mt.validate_latex(r"\int_{0}^{\infty} e^{-x^2} dx = \frac{\sqrt{\pi}}{2}")
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_balanced_nested_braces(self):
        result = mt.validate_latex(r"\frac{\alpha}{\beta + \gamma}")
        assert result["valid"] is True

    def test_empty_string_valid(self):
        result = mt.validate_latex("")
        assert result["valid"] is True


class TestRenderToUnicode:
    def test_greek_alpha(self):
        result = mt.render_to_unicode(r"\alpha")
        assert "\u03b1" in result

    def test_greek_Gamma(self):
        result = mt.render_to_unicode(r"\Gamma")
        assert "\u0393" in result

    def test_infinity_symbol(self):
        result = mt.render_to_unicode(r"\infty")
        assert "\u221e" in result

    def test_integral_symbol(self):
        result = mt.render_to_unicode(r"\int")
        assert "\u222b" in result

    def test_superscript(self):
        result = mt.render_to_unicode(r"x^{2}")
        assert "\u00b2" in result

    def test_subscript(self):
        result = mt.render_to_unicode(r"x_{1}")
        assert "\u2081" in result

    def test_partial_derivative(self):
        result = mt.render_to_unicode(r"\partial")
        assert "\u2202" in result

    def test_nabla(self):
        result = mt.render_to_unicode(r"\nabla")
        assert "\u2207" in result

    def test_arithmetic_expression(self):
        result = mt.render_to_unicode(r"\alpha + \beta = \gamma")
        assert "\u03b1" in result
        assert "\u03b2" in result
        assert "\u03b3" in result

    def test_sum_symbol(self):
        result = mt.render_to_unicode(r"\sum_{i=0}^{n} x_i")
        assert "\u2211" in result

    def test_dollar_signs_stripped(self):
        result = mt.render_to_unicode(r"$\alpha$")
        assert "$" not in result
        assert "\u03b1" in result

    def test_braces_stripped(self):
        result = mt.render_to_unicode(r"\frac{1}{2}")
        assert "{" not in result
        assert "}" not in result

    def test_arrow_commands(self):
        result = mt.render_to_unicode(r"\rightarrow")
        assert "\u2192" in result


class TestParseBibtexEntry:
    def test_parse_article(self):
        entry = (
            '@article{einstein1905, author = "Albert Einstein", '
            'title = "Zur Elektrodynamik", '
            'journal = "Annalen der Physik", year = "1905"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "article"
        assert result["cite_key"] == "einstein1905"
        assert result["fields"]["author"] == "Albert Einstein"
        assert result["fields"]["journal"] == "Annalen der Physik"
        assert result["fields"]["year"] == "1905"

    def test_parse_book(self):
        entry = (
            '@book{knuth1984, author = "Donald Knuth", '
            'title = "The TeXbook", publisher = "Addison-Wesley", '
            'year = "1984"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "book"
        assert result["cite_key"] == "knuth1984"
        assert result["fields"]["publisher"] == "Addison-Wesley"

    def test_parse_inproceedings(self):
        entry = (
            '@inproceedings{he2016deep, author = "Kaiming He", '
            'title = "Deep Residual Learning", '
            'booktitle = "CVPR", year = "2016"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "inproceedings"
        assert result["cite_key"] == "he2016deep"

    def test_parse_phdthesis(self):
        entry = (
            '@phdthesis{witten1976, author = "Edward Witten", '
            'title = "Some Problems in the Theory of Solitons", '
            'school = "Princeton University", year = "1976"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "phdthesis"
        assert result["fields"]["school"] == "Princeton University"

    def test_parse_mastersthesis(self):
        entry = (
            '@mastersthesis{sutskever2013, author = "Ilya Sutskever", '
            'title = "Training RNNs", '
            'school = "University of Toronto", year = "2013"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "mastersthesis"

    def test_parse_techreport(self):
        entry = (
            '@techreport{nash1950, author = "John Nash", '
            'title = "Equilibrium Points in N-person Games", '
            'institution = "Princeton University", year = "1950"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "techreport"
        assert result["fields"]["institution"] == "Princeton University"

    def test_parse_misc(self):
        entry = (
            '@misc{lamport1986, author = "Leslie Lamport", '
            'title = "LaTeX: A Document Preparation System", '
            'year = "1986"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "misc"

    def test_parse_unpublished(self):
        entry = (
            '@unpublished{perelman2002, author = "Grigori Perelman", '
            'title = "The Entropy Formula for the Ricci Flow", '
            'note = "arXiv:math/0211159"}'
        )
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["entry_type"] == "unpublished"

    def test_invalid_entry_returns_none(self):
        result = mt.parse_bibtex_entry("just some text")
        assert result is None

    def test_fields_are_lowercase_keys(self):
        entry = '@article{test, Author = "A", TiTlE = "B", JOURNAL = "C", Year = "2000"}'
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert "author" in result["fields"]
        assert "title" in result["fields"]
        assert "journal" in result["fields"]
        assert "year" in result["fields"]

    def test_extra_whitespace_handled(self):
        entry = '  @article{key,\n  author = "A",\n  title = "B"\n}  '
        result = mt.parse_bibtex_entry(entry)
        assert result is not None
        assert result["fields"]["author"] == "A"


class TestGenerateBibtex:
    def test_generates_article(self):
        result = mt.generate_bibtex(
            "article",
            {"author": "Test Author", "title": "Test Title",
             "journal": "Test Journal", "year": "2024"},
        )
        assert "@article{" in result
        assert "author = {Test Author}" in result
        assert "title = {Test Title}" in result
        assert "journal = {Test Journal}" in result
        assert "year = {2024}" in result

    def test_generates_book(self):
        result = mt.generate_bibtex("book", {"author": "A", "title": "B", "publisher": "C", "year": "2000"})
        assert "@book{" in result
        assert "publisher = {C}" in result

    def test_auto_cite_key_from_author_year(self):
        result = mt.generate_bibtex("article", {"author": "John Smith", "title": "X", "journal": "Y", "year": "2023"})
        assert "Smith2023" in result

    def test_explicit_cite_key(self):
        result = mt.generate_bibtex("misc", {"title": "Stuff"}, cite_key="mykey2024")
        assert "mykey2024" in result

    def test_unknown_author_defaults(self):
        result = mt.generate_bibtex("misc", {"title": "Solo"}, cite_key="k")
        assert "k" in result


class TestGetTemplate:
    def test_aps_template(self):
        t = mt.get_template("APS")
        assert t is not None
        assert t["class"] == "revtex4-2"
        assert "introduction" in t["sections"]

    def test_aip_template(self):
        t = mt.get_template("AIP")
        assert t is not None
        assert t["class"] == "aip-cp"

    def test_ieee_template(self):
        t = mt.get_template("IEEE")
        assert t is not None
        assert t["class"] == "IEEEtran"

    def test_nature_template(self):
        t = mt.get_template("Nature")
        assert t is not None
        assert t["publisher"] == "Nature Publishing Group / Springer Nature"

    def test_science_template(self):
        t = mt.get_template("Science")
        assert t is not None
        assert t["publisher"] == "AAAS"

    def test_case_insensitive(self):
        t = mt.get_template("aps")
        assert t is not None
        assert t["class"] == "revtex4-2"

    def test_unknown_returns_none(self):
        assert mt.get_template("BodegaScience") is None

    def test_template_has_bibliography_style(self):
        for name in ["APS", "AIP", "IEEE", "Nature", "Science"]:
            t = mt.get_template(name)
            assert "bibliography_style" in t, f"{name} missing bibliography_style"


class TestModuleConstants:
    def test_latex_math_commands_populated(self):
        assert len(mt.LATEX_MATH_COMMANDS) > 40
        assert "frac" in mt.LATEX_MATH_COMMANDS
        assert mt.LATEX_MATH_COMMANDS["frac"]["args"] == 2

    def test_latex_environments_populated(self):
        assert len(mt.LATEX_ENVIRONMENTS) > 10
        assert "equation" in mt.LATEX_ENVIRONMENTS

    def test_bibtex_entry_types_populated(self):
        assert len(mt.BIBTEX_ENTRY_TYPES) > 8
        assert "article" in mt.BIBTEX_ENTRY_TYPES
        assert "author" in mt.BIBTEX_ENTRY_TYPES["article"]["mandatory"]

    def test_bibliography_styles_populated(self):
        assert len(mt.BIBLIOGRAPHY_STYLES) >= 5
        assert "plain" in mt.BIBLIOGRAPHY_STYLES
        assert mt.BIBLIOGRAPHY_STYLES["plain"]["label_style"] == "numeric"

    def test_mathjax_katex_map_populated(self):
        assert len(mt.MATHJAX_KATEX_MAP) >= 10
        assert "frac" in mt.MATHJAX_KATEX_MAP
        assert mt.MATHJAX_KATEX_MAP["frac"]["mathjax"] is True
        assert mt.MATHJAX_KATEX_MAP["frac"]["katex"] is True

    def test_amsmath_commands_marked(self):
        assert mt.LATEX_MATH_COMMANDS["mathbb"]["requires_amsmath"] is True
        assert mt.LATEX_MATH_COMMANDS["frac"]["requires_amsmath"] is False

    def test_all_environments_have_description(self):
        for env_name, env_data in mt.LATEX_ENVIRONMENTS.items():
            assert "description" in env_data, f"Environment '{env_name}' missing description"

    def test_all_entry_types_have_mandatory_fields(self):
        for entry_type, entry_data in mt.BIBTEX_ENTRY_TYPES.items():
            assert "mandatory" in entry_data, f"{entry_type} missing mandatory"
            assert "optional" in entry_data, f"{entry_type} missing optional"

    def test_research_templates_have_sections(self):
        for name in ["APS", "AIP", "IEEE", "Nature", "Science"]:
            t = mt.RESEARCH_TEMPLATES.get(name) or mt.RESEARCH_TEMPLATES.get(name.upper())
            assert t is not None
            assert len(t["sections"]) > 5, f"{name} has too few sections"
