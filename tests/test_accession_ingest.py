from __future__ import annotations

import unittest

from formalfinance.sec_accession_ingest import (
    _doc_ix_metadata,
    _taxonomy_from_xsd_docs,
    normalize_accession,
)


class AccessionIngestTests(unittest.TestCase):
    def test_normalize_accession(self) -> None:
        self.assertEqual(normalize_accession("0000123456-26-000001"), "0000123456-26-000001")
        self.assertEqual(normalize_accession("000012345626000001"), "0000123456-26-000001")
        with self.assertRaises(ValueError):
            normalize_accession("abc")

    def test_doc_metadata_detection(self) -> None:
        text = """
        <html><body>
        <ix:header></ix:header>
        <script>alert(1)</script>
        <a href="https://example.com/a.js">x</a>
        </body></html>
        """
        meta = _doc_ix_metadata("example.htm", text)
        self.assertTrue(meta["is_inline_xbrl"])
        self.assertTrue(meta["contains_ix_header"])
        self.assertIn("script", meta["disallowed_html_tags"])
        self.assertIn("https://example.com/a.js", meta["external_references"])

    def test_xsd_taxonomy_extraction(self) -> None:
        xsd = """
        <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
                   xmlns:ff="http://formalfinance.example/taxonomy/2025"
                   targetNamespace="http://formalfinance.example/taxonomy/2025">
          <xs:element name="AdjustedRevenue"/>
          <xs:element name="AdjustedEbitda"/>
        </xs:schema>
        """
        taxonomy = _taxonomy_from_xsd_docs(
            xsd_docs=[("ff-2025.xsd", xsd)],
            fact_concepts={"dei:DocumentType"},
        )
        concepts = {item["concept"] for item in taxonomy["elements"]}
        self.assertIn("ff:AdjustedRevenue", concepts)
        self.assertIn("ff:AdjustedEbitda", concepts)
        self.assertIn("dei:DocumentType", concepts)


if __name__ == "__main__":
    unittest.main()
