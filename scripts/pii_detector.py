#!/usr/bin/env python3
"""
PII Detection Script using Presidio

This script scans files for Personally Identifiable Information (PII) using Microsoft's Presidio library.
It detects various types of sensitive information including:

- Names
- Email addresses
- Phone numbers
- Credit card numbers
- Social Security Numbers
- IP addresses
- API keys and tokens
- File paths containing usernames
- And more...

Usage:
    python scripts/pii_detector.py [file1] [file2] ... [--exclude pattern1 pattern2]

Examples:
    # Scan all Python files
    python scripts/pii_detector.py --glob "*.py"

    # Scan specific files
    python scripts/pii_detector.py README.md config.py

    # Scan with exclusions
    python scripts/pii_detector.py --glob "*.py" --exclude "*test*" "*__pycache__*"
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

# Try to import Presidio, fall back to basic regex if not available
PRESIDIO_AVAILABLE = False
try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer,
        EmailRecognizer,
        IbanRecognizer,
        IpRecognizer,
        PhoneRecognizer,
        UrlRecognizer,
    )

    PRESIDIO_AVAILABLE = True
except ImportError:
    # Define dummy classes for type hints when Presidio is not available
    class AnalyzerEngine:
        pass

    class PatternRecognizer:
        pass

    class RecognizerRegistry:
        pass

    print("⚠️  Presidio not installed. Using basic regex patterns.")
    print("Install full PII detection with: uv sync --extra dev")

# Try to load spaCy model for enhanced analysis
SPACY_AVAILABLE = False
if PRESIDIO_AVAILABLE:
    try:
        import spacy

        nlp = spacy.load("en_core_web_lg")
        SPACY_AVAILABLE = True
    except OSError:
        print(
            "⚠️  spaCy model 'en_core_web_lg' not found. Install with: python -m spacy download en_core_web_lg"
        )
        print("Continuing with basic Presidio analysis...")


class PIIDetector:
    """PII Detection using Presidio with fallback to regex patterns."""

    def __init__(self):
        if PRESIDIO_AVAILABLE and SPACY_AVAILABLE:
            self.analyzer = self._setup_presidio_analyzer()
            self.use_presidio = True
        else:
            self.patterns = self._setup_regex_patterns()
            self.use_presidio = False

    def _setup_presidio_analyzer(self) -> "AnalyzerEngine":
        """Set up Presidio analyzer with custom recognizers."""

        # Initialize with spaCy NLP engine
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
            }
        ).create_engine()

        # Create registry with default recognizers
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers()

        # Add custom recognizers for API keys and file paths
        api_key_recognizer = PatternRecognizer(
            supported_entity="API_KEY",
            patterns=[
                # OpenAI API keys (sk-...)
                {
                    "name": "openai_api_key",
                    "regex": r"sk-[a-zA-Z0-9]{48}",
                    "score": 0.95,
                },
                # OpenRouter API keys (sk-or-v1-...)
                {
                    "name": "openrouter_api_key",
                    "regex": r"sk-or-v1-[a-zA-Z0-9]{64}",
                    "score": 0.95,
                },
                # Generic API keys (long alphanumeric strings that look like keys)
                {
                    "name": "generic_api_key",
                    "regex": r"(?i)(api[_-]?key|token|secret|password)[\s]*[=:][\s]*['\"]?([a-zA-Z0-9]{32,})['\"]?",
                    "score": 0.8,
                },
            ],
        )

        file_path_recognizer = PatternRecognizer(
            supported_entity="FILE_PATH",
            patterns=[
                # Unix file paths with usernames
                {
                    "name": "unix_user_path",
                    "regex": r"/Users/[^/\s]+/",
                    "score": 0.9,
                },
                # Windows file paths with usernames
                {
                    "name": "windows_user_path",
                    "regex": r"C:\\Users\\[^\\\s]+\\",
                    "score": 0.9,
                },
                # Generic user home paths
                {
                    "name": "home_path",
                    "regex": r"~/[^/\s]+/",
                    "score": 0.7,
                },
            ],
        )

        registry.add_recognizer(api_key_recognizer)
        registry.add_recognizer(file_path_recognizer)

        # Use basic NLP engine if spaCy not available
        if not SPACY_AVAILABLE:
            nlp_engine = None

        return AnalyzerEngine(
            registry=registry, nlp_engine=nlp_engine, supported_languages=["en"]
        )

    def _setup_regex_patterns(self) -> Dict[str, List[Dict]]:
        """Set up regex patterns for PII detection when Presidio is not available."""
        return {
            "API_KEY": [
                {
                    "name": "openai_api_key",
                    "pattern": r"sk-[a-zA-Z0-9]{48}",
                    "score": 0.95,
                },
                {
                    "name": "openrouter_api_key",
                    "pattern": r"sk-or-v1-[a-zA-Z0-9]{64}",
                    "score": 0.95,
                },
                {
                    "name": "generic_api_key",
                    "pattern": r"(?i)(api[_-]?key|token|secret|password)[\s]*[=:][\s]*['\"]?([a-zA-Z0-9]{32,})['\"]?",
                    "score": 0.8,
                },
            ],
            "EMAIL": [
                {
                    "name": "email_address",
                    "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                    "score": 0.9,
                },
            ],
            "PHONE": [
                {
                    "name": "phone_number",
                    "pattern": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
                    "score": 0.8,
                },
            ],
            "CREDIT_CARD": [
                {
                    "name": "credit_card",
                    "pattern": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
                    "score": 0.85,
                },
            ],
            "FILE_PATH": [
                {
                    "name": "unix_user_path",
                    "pattern": r"/Users/[^/\s]+/",
                    "score": 0.9,
                },
                {
                    "name": "windows_user_path",
                    "pattern": r"C:\\Users\\[^\\\s]+\\",
                    "score": 0.9,
                },
            ],
            "IP_ADDRESS": [
                {
                    "name": "ipv4_address",
                    "pattern": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
                    "score": 0.8,
                },
            ],
        }

    def scan_file(self, file_path: str) -> Dict:
        """Scan a single file for PII."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            return {
                "file": file_path,
                "error": f"Could not read file: {e}",
                "pii_found": [],
                "has_pii": False,
            }

        if not content.strip():
            return {"file": file_path, "pii_found": [], "has_pii": False}

        # Use appropriate scanning method
        if self.use_presidio:
            pii_found = self._scan_with_presidio(content)
        else:
            pii_found = self._scan_with_regex(content)

        return {
            "file": file_path,
            "pii_found": pii_found,
            "has_pii": len(pii_found) > 0,
        }

    def _scan_with_presidio(self, content: str) -> List[Dict]:
        """Scan content using Presidio analyzer."""
        results = self.analyzer.analyze(
            text=content, language="en", return_detailed_analysis=True
        )

        # Convert results to our format
        pii_found = []
        for result in results:
            pii_found.append(
                {
                    "entity_type": result.entity_type,
                    "confidence": result.score,
                    "start": result.start,
                    "end": result.end,
                    "text": (
                        content[result.start : result.end]
                        if result.start < len(content)
                        else ""
                    ),
                    "line_number": self._get_line_number(content, result.start),
                }
            )

        return pii_found

    def _scan_with_regex(self, content: str) -> List[Dict]:
        """Scan content using regex patterns."""
        pii_found = []

        for entity_type, patterns in self.patterns.items():
            for pattern_info in patterns:
                pattern = pattern_info["pattern"]
                score = pattern_info["score"]
                name = pattern_info["name"]

                for match in re.finditer(
                    pattern, content, re.MULTILINE | re.IGNORECASE
                ):
                    text = match.group()

                    # Skip false positives
                    if self._is_false_positive(entity_type, text, content):
                        continue

                    pii_found.append(
                        {
                            "entity_type": entity_type,
                            "confidence": score,
                            "start": match.start(),
                            "end": match.end(),
                            "text": text,
                            "line_number": self._get_line_number(
                                content, match.start()
                            ),
                        }
                    )

        return pii_found

    def _is_false_positive(self, entity_type: str, text: str, content: str) -> bool:
        """Check if a potential PII match is a false positive."""
        # Skip author emails in project metadata
        if entity_type == "EMAIL" and "authors" in content and "@agency42.co" in text:
            return True

        # Skip example file paths (containing "Desktop/lab/varela" or similar patterns)
        if entity_type == "FILE_PATH" and (
            "Desktop/lab/varela" in text or "example" in content.lower()
        ):
            return True

        # Skip common test/example patterns
        if "test" in content.lower() and len(content.split()) < 10:
            return True

        return False

    def _get_line_number(self, content: str, position: int) -> int:
        """Get the line number for a given position in the content."""
        return content[:position].count("\n") + 1

    def scan_files(
        self, file_paths: List[str], exclude_patterns: Optional[List[str]] = None
    ) -> Dict:
        """Scan multiple files for PII."""
        exclude_patterns = exclude_patterns or []

        results = []
        total_files = 0
        files_with_pii = 0

        for file_path in file_paths:
            # Check if file should be excluded
            if self._should_exclude(file_path, exclude_patterns):
                continue

            total_files += 1
            result = self.scan_file(file_path)

            if result.get("has_pii", False):
                files_with_pii += 1

            results.append(result)

        return {
            "summary": {
                "total_files_scanned": total_files,
                "files_with_pii": files_with_pii,
                "clean_files": total_files - files_with_pii,
            },
            "results": results,
        }

    def _should_exclude(self, file_path: str, exclude_patterns: List[str]) -> bool:
        """Check if a file should be excluded based on patterns."""
        for pattern in exclude_patterns:
            if pattern in file_path:
                return True
        return False


def find_files(
    patterns: List[str], exclude_patterns: Optional[List[str]] = None
) -> List[str]:
    """Find files matching the given patterns."""
    files = []
    exclude_patterns = exclude_patterns or []

    for pattern in patterns:
        matched_files = glob.glob(pattern, recursive=True)
        for file_path in matched_files:
            # Check exclusions
            should_exclude = False
            for exclude_pattern in exclude_patterns:
                if exclude_pattern in file_path:
                    should_exclude = True
                    break

            if not should_exclude and os.path.isfile(file_path):
                files.append(file_path)

    return sorted(list(set(files)))


def main():
    parser = argparse.ArgumentParser(
        description="Scan files for Personally Identifiable Information (PII)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan all Python files
  python scripts/pii_detector.py --glob "*.py"

  # Scan specific files
  python scripts/pii_detector.py README.md config.py

  # Scan with exclusions
  python scripts/pii_detector.py --glob "*.py" --exclude "*test*" "*__pycache__*"

  # JSON output for CI/CD
  python scripts/pii_detector.py --glob "*.py" --json
        """,
    )

    parser.add_argument("files", nargs="*", help="Specific files to scan")

    parser.add_argument(
        "--glob",
        "-g",
        action="append",
        help="Glob patterns to match files (can be used multiple times)",
    )

    parser.add_argument(
        "--exclude",
        "-e",
        action="append",
        help="Patterns to exclude from scanning (can be used multiple times)",
    )

    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output results in JSON format for CI/CD integration",
    )

    parser.add_argument(
        "--fail-on-pii",
        action="store_true",
        help="Exit with non-zero code if PII is found (for CI/CD)",
    )

    args = parser.parse_args()

    # Collect files to scan
    files_to_scan = []

    if args.files:
        files_to_scan.extend(args.files)

    if args.glob:
        glob_files = find_files(args.glob, args.exclude)
        files_to_scan.extend(glob_files)

    if not files_to_scan:
        print("❌ No files specified. Use --glob or provide file names.")
        print("Run with --help for examples.")
        sys.exit(1)

    # Remove duplicates and filter out non-existent files
    files_to_scan = sorted(list(set(files_to_scan)))
    files_to_scan = [f for f in files_to_scan if os.path.isfile(f)]

    if not files_to_scan:
        print("❌ No valid files found to scan.")
        sys.exit(1)

    # Initialize detector
    print(f"🔍 Scanning {len(files_to_scan)} files for PII...")
    detector = PIIDetector()

    # Scan files
    results = detector.scan_files(files_to_scan, args.exclude)

    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Human-readable output
        summary = results["summary"]
        print(f"\n📊 Scan Results:")
        print(f"   Files scanned: {summary['total_files_scanned']}")
        print(f"   Files with PII: {summary['files_with_pii']}")
        print(f"   Clean files: {summary['clean_files']}")

        if summary["files_with_pii"] > 0:
            print(f"\n⚠️  PII Found in {summary['files_with_pii']} files:")
            print("-" * 50)

            for result in results["results"]:
                if result.get("has_pii", False):
                    print(f"\n📁 {result['file']}:")
                    for pii in result["pii_found"]:
                        entity_type = pii["entity_type"]
                        confidence = pii["confidence"]
                        line = pii["line_number"]
                        text = (
                            pii["text"][:50] + "..."
                            if len(pii["text"]) > 50
                            else pii["text"]
                        )

                        print(
                            f"   🔴 {entity_type} (confidence: {confidence:.2f}) at line {line}: {text}"
                        )
        else:
            print("\n✅ No PII detected!")

    # Exit with appropriate code for CI/CD
    if args.fail_on_pii and results["summary"]["files_with_pii"] > 0:
        print("\n❌ PII detected! Failing build.")
        sys.exit(1)
    elif results["summary"]["files_with_pii"] == 0:
        print("\n✅ All files are clean!")
    else:
        print(f"\n⚠️  PII detected in {results['summary']['files_with_pii']} files.")


if __name__ == "__main__":
    main()
