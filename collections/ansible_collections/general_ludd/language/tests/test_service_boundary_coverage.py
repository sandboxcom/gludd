"""Expose canonical language unit contracts to the collection acceptance gate.

The language gate uses an explicit path list rather than discovering every
``test_language_*`` module.  Re-exporting the canonical classes keeps one test
implementation while ensuring the collection's public gate measures every
daemon-owned implementation reached by the controller action plugin.
"""

from __future__ import annotations

from tests.unit import (
    test_language_contracts as contracts_tests,
)
from tests.unit import (
    test_language_core as core_tests,
)
from tests.unit import (
    test_language_detection as detection_tests,
)
from tests.unit import (
    test_language_operations as operation_tests,
)
from tests.unit import (
    test_language_translation as translation_tests,
)
from tests.unit import (
    test_language_transliteration as transliteration_tests,
)

TestDetectLanguage = detection_tests.TestDetectLanguage
TestLanguageNames = detection_tests.TestLanguageNames
TestScriptToLanguages = detection_tests.TestScriptToLanguages
TestDetectLanguageDeep = detection_tests.TestDetectLanguageDeep
TestDetectLanguagesInText = detection_tests.TestDetectLanguagesInText
TestSplitSentences = detection_tests.TestSplitSentences
TestScriptOf = detection_tests.TestScriptOf
TestPrimaryScript = detection_tests.TestPrimaryScript
TestCountScriptChars = detection_tests.TestCountScriptChars
TestStopwordPairwise = detection_tests.TestStopwordPairwise
TestFrequencyFallback = detection_tests.TestFrequencyFallback
TestUnicodeNameToScript = detection_tests.TestUnicodeNameToScript
TestStopwordTables = detection_tests.TestStopwordTables
TestFrequencyProfiles = detection_tests.TestFrequencyProfiles
TestUnknownInput = detection_tests.TestUnknownInput

TestTranslate = translation_tests.TestTranslate
TestMultiWordTranslation = translation_tests.TestMultiWordTranslation
TestDictionaryData = translation_tests.TestDictionaryData

TestTransliterate = transliteration_tests.TestTransliterate
TestListSchemes = transliteration_tests.TestListSchemes
TestTransliterationTables = transliteration_tests.TestTransliterationTables

TestLanguageDetectionContracts = contracts_tests.TestLanguageDetectionContracts
TestTranslationContracts = contracts_tests.TestTranslationContracts
TestTransliterationContracts = contracts_tests.TestTransliterationContracts
TestScriptDetectionContracts = contracts_tests.TestScriptDetectionContracts
TestContractExports = contracts_tests.TestContractExports

TestLanguageDetector = core_tests.TestLanguageDetector
TestTranslatorConstruction = core_tests.TestTranslatorConstruction
TestTranslatorTranslate = core_tests.TestTranslatorTranslate
TestTranslatorMockFeatures = core_tests.TestTranslatorMockFeatures
TestTransliteratorConstruction = core_tests.TestTransliteratorConstruction
TestTransliteratorTransliterate = core_tests.TestTransliteratorTransliterate
TestTransliteratorScriptDetection = core_tests.TestTransliteratorScriptDetection
TestTransliteratorMockFeatures = core_tests.TestTransliteratorMockFeatures

test_language_detect_translate_and_transliterate_preserve_public_schemas = (
    operation_tests.test_language_detect_translate_and_transliterate_preserve_public_schemas
)
test_bom_and_encoding_accept_managed_host_slurp_payloads = (
    operation_tests.test_bom_and_encoding_accept_managed_host_slurp_payloads
)
test_homoglyph_unicode_locale_and_phonetic_results_remain_structured = (
    operation_tests.test_homoglyph_unicode_locale_and_phonetic_results_remain_structured
)
test_unicode_analysis_decodes_managed_host_slurp_payload = (
    operation_tests.test_unicode_analysis_decodes_managed_host_slurp_payload
)
test_unicode_analysis_rejects_non_utf8_managed_host_input = (
    operation_tests.test_unicode_analysis_rejects_non_utf8_managed_host_input
)
test_operation_service_rejects_unknown_or_unbounded_inputs = (
    operation_tests.test_operation_service_rejects_unknown_or_unbounded_inputs
)
