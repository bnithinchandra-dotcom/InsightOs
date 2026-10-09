import os
import unittest
from io import BytesIO
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl import Workbook

from app.parsing import ParsingError, parse_dataset_file


def parse(filename: str, content: bytes):
    return parse_dataset_file(filename, BytesIO(content), len(content))


class ReadTrackingStream(BytesIO):
    def __init__(self, content: bytes):
        super().__init__(content)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        result = super().read(size)
        self.bytes_read += len(result)
        return result


def make_xlsx() -> bytes:
    workbook = Workbook()
    first = workbook.active
    first.title = "People"
    first.append(["odd header", "value", "formula"])
    first.append(["Ada", 3, "=1+2"])
    first.append(["Lin", 5, None])
    second = workbook.create_sheet("Empty")
    second.append(["name"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def make_parquet() -> bytes:
    output = BytesIO()
    table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "label": pa.array(["a", "b"], type=pa.string()),
        }
    )
    pq.write_table(table, output)
    return output.getvalue()


class ParsingTests(unittest.TestCase):
    def test_csv_and_tsv_preserve_headers_and_text_types(self):
        csv_result = parse(
            "records.csv",
            b'"odd, header",,amount\r\n"first",x,1\r\n"second",,2\r\n',
        )
        self.assertEqual(csv_result.row_count, 2)
        self.assertEqual(
            [column["name"] for column in csv_result.columns],
            ["odd, header", "", "amount"],
        )
        self.assertEqual(
            [column["physical_type"] for column in csv_result.columns],
            ["string", "string", "string"],
        )
        self.assertEqual(csv_result.columns[1]["empty_values"], 1)

        tsv_result = parse("records.tsv", b"id\tname\r\n1\tAda\r\n2\tLin\r\n")
        self.assertEqual(tsv_result.row_count, 2)
        self.assertEqual(tsv_result.metadata["delimiter"], "tab")

    def test_json_structures_and_physical_types(self):
        result = parse(
            "records.json",
            b'[{"id":1,"name":"Ada","nested":{"ok":true}},'
            b'{"id":2,"name":"","extra":null}]',
        )
        self.assertEqual(result.row_count, 2)
        self.assertEqual(
            [(column["name"], column["physical_type"]) for column in result.columns],
            [
                ("id", "integer"),
                ("name", "string"),
                ("nested", "object"),
                ("extra", "null"),
            ],
        )
        self.assertEqual(result.columns[2]["missing_values"], 1)
        self.assertEqual(result.columns[1]["empty_values"], 1)

        wrapped = parse(
            "wrapped.json",
            b'{"records":[{"x":true},{"x":false}]}',
        )
        self.assertEqual(wrapped.metadata["record_path"], "$.records")
        self.assertEqual(wrapped.columns[0]["physical_type"], "boolean")
        self.assertEqual(wrapped.columns[0]["position"], 0)
        self.assertEqual(wrapped.row_count, 2)

        single = parse("single.json", b'{"a":1,"b":null}')
        self.assertEqual(single.row_count, 1)
        self.assertEqual(single.columns[1]["physical_type"], "null")

    def test_json_rejects_lossy_wrapper_siblings_and_preserves_nested_values(self):
        nested_record = parse(
            "nested-records.json",
            b'[{"profile":{"name":"Ada"},"tags":["engineer","writer"]}]',
        )
        self.assertEqual(nested_record.row_count, 1)
        self.assertEqual(
            [column["physical_type"] for column in nested_record.columns],
            ["object", "array"],
        )

        nested_wrapper = parse(
            "nested-wrapper.json",
            b'{"records":[{"profile":{"name":"Ada"}}]}',
        )
        self.assertEqual(nested_wrapper.columns[0]["physical_type"], "object")

        for payload in (
            b'{"records":[{"x":1}],"source":"fixture"}',
            b'{"records":[{"x":1}],"extra":[1,2]}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ParsingError) as context:
                    parse("lossy.json", payload)
                self.assertEqual(
                    context.exception.code,
                    "unsupported_json_structure",
                )

    def test_xlsx_reads_worksheets_and_reports_formula_cells(self):
        result = parse("book.xlsx", make_xlsx())
        self.assertEqual(result.row_count, 2)
        self.assertEqual(
            [sheet["name"] for sheet in result.metadata["worksheets"]],
            ["People", "Empty"],
        )
        self.assertEqual(result.columns[0]["table"], "People")
        self.assertEqual(result.columns[2]["physical_type"], "formula")

    def test_parquet_uses_schema_and_scans_records(self):
        result = parse("table.parquet", make_parquet())
        self.assertEqual(result.row_count, 2)
        self.assertEqual(
            [(column["name"], column["physical_type"]) for column in result.columns],
            [("id", "int64"), ("label", "string")],
        )
        self.assertEqual(result.metadata["rows_scanned"], 2)

    def test_profiles_are_bounded_and_deterministic_across_supported_formats(self):
        csv_result = parse(
            "profile.csv",
            b"amount,label,optional\n1,A,\n2,B,x\n2,B,x\n",
        )
        csv_profile = csv_result.profile_result
        csv_table = csv_profile["tables"][0]
        self.assertEqual(csv_profile["row_count"], 3)
        self.assertEqual(csv_table["duplicate_row_count"], 1)
        self.assertEqual(csv_table["columns"][0]["physical_types"], ["string"])
        self.assertEqual(csv_table["columns"][0]["distinct_value_count"], 2)
        self.assertTrue(csv_table["columns"][0]["distinct_count_exact"])
        self.assertEqual(
            csv_table["columns"][0]["numeric_statistics"],
            {
                "minimum": 1,
                "maximum": 2,
                "mean": 5 / 3,
                "median": 2,
                "median_exact": True,
            },
        )
        self.assertEqual(csv_table["columns"][2]["empty_value_count"], 1)
        self.assertEqual(
            csv_table["columns"][1]["categorical_summary"][0]["value"],
            "B",
        )

        tsv_result = parse("profile.tsv", b"value\tkind\n4\talpha\n4\talpha\n")
        self.assertEqual(
            tsv_result.profile_result["tables"][0]["duplicate_row_count"],
            1,
        )

        json_result = parse(
            "profile.json",
            b'[{"n":1,"kind":"a"},{"n":null,"kind":""},'
            b'{"n":2,"kind":"a"},{"n":2,"kind":"a"},{"kind":"a"}]',
        )
        json_table = json_result.profile_result["tables"][0]
        self.assertEqual(json_table["duplicate_row_count"], 1)
        self.assertEqual(json_table["columns"][0]["missing_value_count"], 2)
        self.assertEqual(json_table["columns"][0]["numeric_statistics"]["mean"], 5 / 3)
        self.assertEqual(json_table["columns"][1]["empty_value_count"], 1)

        mixed_numeric = parse(
            "mixed-numeric.json",
            b'[{"n":1},{"n":1.5}]',
        ).profile_result["tables"][0]["columns"][0]
        self.assertEqual(
            mixed_numeric["physical_types"],
            ["integer", "number"],
        )
        self.assertEqual(
            mixed_numeric["numeric_statistics"],
            {
                "minimum": 1,
                "maximum": 1.5,
                "mean": 1.25,
                "median": 1.25,
                "median_exact": True,
            },
        )

        mixed = parse(
            "mixed.json",
            b'[{"value":1},{"value":"2"},{"value":"not-a-number"}]',
        )
        mixed_column = mixed.profile_result["tables"][0]["columns"][0]
        self.assertIsNone(mixed_column["numeric_statistics"])
        self.assertIsNotNone(mixed_column["numeric_statistics_note"])
        self.assertTrue(mixed.profile_result["warnings"])

        nested_profile = parse(
            "nested-profile.json",
            b'[{"value":{"items":[1,true]}},{"value":{"items":[1,true]}}]',
        ).profile_result["tables"][0]
        self.assertEqual(nested_profile["duplicate_row_count"], 1)
        self.assertEqual(nested_profile["columns"][0]["distinct_value_count"], 1)

        xlsx_result = parse("profile.xlsx", make_xlsx())
        xlsx_profile = xlsx_result.profile_result
        self.assertEqual([table["name"] for table in xlsx_profile["tables"]], ["People", "Empty"])
        self.assertEqual(xlsx_profile["row_count"], 2)
        self.assertEqual(
            xlsx_profile["tables"][0]["columns"][1]["physical_types"],
            ["integer"],
        )
        self.assertTrue(
            any("formula cells" in warning for warning in xlsx_profile["warnings"])
        )

        parquet_result = parse("profile.parquet", make_parquet())
        parquet_profile_column = parquet_result.profile_result["tables"][0]["columns"][0]
        self.assertEqual(
            parquet_profile_column["numeric_statistics"]["minimum"],
            1,
        )

        xml_result = parse(
            "profile.xml",
            b"<root><record><value>3</value></record>"
            b"<record><value>5</value></record><record/></root>",
        )
        self.assertEqual(
            xml_result.profile_result["tables"][0]["columns"][0][
                "missing_value_count"
            ],
            1,
        )
        self.assertEqual(
            xml_result.profile_result["tables"][0]["columns"][0][
                "numeric_statistics"
            ]["median"],
            4,
        )

    def test_profiles_empty_files_with_headers_and_report_bounded_analysis(self):
        empty_rows = parse("empty-rows.csv", b"value,label\n")
        empty_table = empty_rows.profile_result["tables"][0]
        self.assertEqual(empty_table["row_count"], 0)
        self.assertEqual(empty_table["column_count"], 2)
        self.assertEqual(empty_table["duplicate_row_count"], 0)

        with patch.dict(
            os.environ,
            {
                "MAX_PROFILE_DISTINCT_VALUES": "1",
                "MAX_PROFILE_NUMERIC_VALUES": "1",
                "MAX_PROFILE_DUPLICATE_ROWS": "1",
            },
        ):
            bounded = parse(
                "bounded.csv",
                b"value,label\n1,alpha\n2,beta\n2,beta\n",
            ).profile_result
        bounded_table = bounded["tables"][0]
        self.assertIsNone(bounded_table["duplicate_row_count"])
        self.assertIsNone(bounded_table["columns"][0]["distinct_value_count"])
        self.assertIsNone(bounded_table["columns"][0]["numeric_statistics"]["median"])
        self.assertEqual(bounded_table["columns"][0]["numeric_statistics"]["minimum"], 1)
        self.assertTrue(bounded["warnings"])
        self.assertTrue(bounded["unsupported_analyses"])

        categorical = parse(
            "categories.csv",
            ("category\n" + "".join(f"value-{index}\n" for index in range(20))).encode(),
        ).profile_result["tables"][0]["columns"][0]["categorical_summary"]
        self.assertLessEqual(len(categorical), 10)

    def test_xml_namespaces_attributes_nested_and_missing_values(self):
        content = (
            b'<d:root xmlns:d="urn:records">'
            b'<d:record id="1"><d:name>Ada</d:name>'
            b'<d:address><d:city>London</d:city></d:address>'
            b'<d:note/></d:record>'
            b'<d:record id="2"><d:name></d:name>'
            b'<d:address/><d:other>kept</d:other></d:record>'
            b'</d:root>'
        )
        result = parse("records.xml", content)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.metadata["record_element"], "{urn:records}record")
        self.assertIn("@id", [column["name"] for column in result.columns])
        self.assertIn(
            "{urn:records}address/{urn:records}city",
            [column["name"] for column in result.columns],
        )
        city = next(
            column
            for column in result.columns
            if column["name"] == "{urn:records}address/{urn:records}city"
        )
        self.assertEqual(city["missing_values"], 1)
        name = next(column for column in result.columns if column["name"].endswith("name"))
        self.assertEqual(name["empty_values"], 1)
        self.assertIn(
            "{urn:records}note",
            [column["name"] for column in result.columns],
        )

    def test_malformed_empty_mismatched_and_ambiguous_inputs_fail(self):
        for extension in ("csv", "tsv", "xlsx", "json", "parquet", "xml"):
            with self.subTest(empty_extension=extension):
                with self.assertRaises(ParsingError) as context:
                    parse(f"empty.{extension}", b"")
                self.assertEqual(context.exception.code, "empty_file")

        cases = [
            ("broken.csv", b'"unterminated', "malformed_delimited_text"),
            ("wrong.csv", b'{"records":[{"a":1}]}', "content_mismatch"),
            ("empty-json-array.csv", b"[]", "content_mismatch"),
            ("broken.json", b'{"a":', "malformed_json"),
            ("broken.xlsx", b"not an xlsx", "corrupt_xlsx"),
            ("broken.parquet", b"not parquet", "content_mismatch"),
            ("corrupt.parquet", b"PAR1invalidPAR1", "corrupt_parquet"),
            ("broken.xml", b"<root>", "malformed_xml"),
            (
                "xml-content.csv",
                b"<root><record>one</record><record>two</record></root>",
                "content_mismatch",
            ),
            (
                "ambiguous.xml",
                b"<root><a><x>1</x><x>2</x></a><b><y>1</y><y>2</y></b></root>",
                "ambiguous_xml_records",
            ),
        ]
        for filename, content, expected_code in cases:
            with self.subTest(filename=filename):
                with self.assertRaises(ParsingError) as context:
                    parse(filename, content)
                self.assertEqual(context.exception.code, expected_code)

    def test_xml_rejects_internal_dtd_and_external_entities(self):
        payloads = (
            b'<!DOCTYPE root [<!ELEMENT root ANY>]>'
            b"<root><record>one</record><record>two</record></root>",
            b'<!DOCTYPE root [<!ENTITY external SYSTEM "file:///never-read">]>'
            b"<root><record>&external;</record><record>value</record></root>",
            b'<!DOCTYPE root SYSTEM "file:///never-read">'
            b"<root><record>one</record><record>two</record></root>",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ParsingError) as context:
                    parse("doctype.xml", payload)
                self.assertEqual(context.exception.code, "malformed_xml")

    def test_ordinary_xml_remains_supported_and_csv_single_column_is_valid(self):
        result = parse(
            "ordinary.xml",
            b'<?xml version="1.0"?><root><record id="1">one</record>'
            b'<record id="2">two</record></root>',
        )
        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.columns[0]["name"], "@id")

        csv_result = parse("single-column.csv", b"name\nAda\nLin\n")
        self.assertEqual([column["name"] for column in csv_result.columns], ["name"])
        self.assertEqual(csv_result.row_count, 2)

        with self.assertRaises(ParsingError) as context:
            parse("empty.csv", b"")
        self.assertEqual(context.exception.code, "empty_file")


    def test_configured_row_and_json_depth_limits(self):
        with patch.dict(os.environ, {"MAX_DATASET_ROWS": "1"}):
            with self.assertRaises(ParsingError) as context:
                parse("rows.json", b'[{"a":1},{"a":2}]')
        self.assertEqual(context.exception.code, "resource_limit")

        deep_xml = (
            b"<root><record><nested><value>x</value></nested></record>"
            b"<record><nested><value>y</value></nested></record></root>"
            + b" " * (128 * 1024)
        )
        depth_stream = ReadTrackingStream(deep_xml)
        with patch.dict(os.environ, {"MAX_XML_DEPTH": "2"}):
            with self.assertRaises(ParsingError) as context:
                parse_dataset_file("deep.xml", depth_stream, len(deep_xml))
        self.assertEqual(context.exception.code, "resource_limit")
        self.assertLess(depth_stream.bytes_read, len(deep_xml))

        many_elements = b"<root>" + b"<record>x</record>" * 20_000 + b"</root>"
        element_stream = ReadTrackingStream(many_elements)
        with patch.dict(os.environ, {"MAX_XML_ELEMENTS": "3"}):
            with self.assertRaises(ParsingError) as context:
                parse_dataset_file("many.xml", element_stream, len(many_elements))
        self.assertEqual(context.exception.code, "resource_limit")
        self.assertLess(element_stream.bytes_read, len(many_elements))

        with patch.dict(os.environ, {"MAX_XML_SIZE_MB": "1"}):
            with self.assertRaises(ParsingError) as context:
                parse("large.xml", b"<root/>" * 160_000)
        self.assertEqual(context.exception.code, "resource_limit")

        with patch.dict(os.environ, {"MAX_JSON_DEPTH": "2"}):
            with self.assertRaises(ParsingError) as context:
                parse("deep.json", b'{"a":{"b":{"c":1}}}')
        self.assertEqual(context.exception.code, "resource_limit")

    def test_duplicate_json_keys_are_not_silently_overwritten(self):
        with self.assertRaises(ParsingError) as context:
            parse("duplicate.json", b'[{"key":1,"key":2}]')
        self.assertEqual(context.exception.code, "malformed_json")


if __name__ == "__main__":
    unittest.main()
