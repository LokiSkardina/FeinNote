# Responsible for: genuine OpenXML DOCX and Qt-rendered Unicode PDF documents.

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from core.exporter.base_exporter import BaseExporter, final_text


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class DocxExporter(BaseExporter):
    """Create a self-contained WordprocessingML package using Python's standard library."""

    def render(self, note) -> bytes:
        """Return a real DOCX preserving paragraphs, line breaks, tabs and Unicode."""
        def word(name, attributes=None):
            return ET.Element(f"{{{WORD_NS}}}{name}",
                              {f"{{{WORD_NS}}}{key}": str(value) for key, value in (attributes or {}).items()})

        document = word("document")
        body = ET.SubElement(document, f"{{{WORD_NS}}}body")
        text = final_text(note).replace("\r\n", "\n").replace("\r", "\n")
        for paragraph_text in text.split("\n\n"):
            paragraph = word("p")
            body.append(paragraph)
            run = word("r")
            paragraph.append(run)
            for line_index, line in enumerate(paragraph_text.split("\n")):
                if line_index:
                    run.append(word("br"))
                for index, part in enumerate(line.split("\t")):
                    if index:
                        run.append(word("tab"))
                    element = word("t")
                    element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    element.text = part
                    run.append(element)
        section = word("sectPr")
        section.append(word("pgSz", {"w": 11906, "h": 16838}))
        section.append(word("pgMar", {"top": 1020, "right": 1020, "bottom": 1020, "left": 1020,
                                      "header": 500, "footer": 500, "gutter": 0}))
        body.append(section)
        styles = word("styles")
        normal = word("style", {"type": "paragraph", "default": "1", "styleId": "Normal"})
        normal.append(word("name", {"val": "Normal"}))
        properties = word("rPr")
        properties.append(word("rFonts", {"ascii": "Calibri", "hAnsi": "Calibri", "cs": "Calibri"}))
        properties.append(word("sz", {"val": 22}))
        normal.append(properties)
        styles.append(normal)
        # OPC relationships identify this as an Office document rather than a
        # renamed text file. No external resources or optional library are needed.
        content_types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
        relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
        document_relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", content_types)
            package.writestr("_rels/.rels", relationships)
            package.writestr("word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True))
            package.writestr("word/styles.xml", ET.tostring(styles, encoding="utf-8", xml_declaration=True))
            package.writestr("word/_rels/document.xml.rels", document_relationships)
        return output.getvalue()


class PdfExporter(BaseExporter):
    """Lay out the final note as a paginated Unicode PDF through Qt."""

    def render(self, note) -> bytes:
        """Return an A4 PDF with 18 mm margins, wrapping and embedded Qt fonts."""
        from PyQt6.QtCore import QBuffer, QIODevice, QMarginsF, QSizeF
        from PyQt6.QtGui import QFont, QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument, QTextOption

        if QGuiApplication.instance() is None:
            raise RuntimeError("PDF export requires the running application.")
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError("Could not open the PDF output buffer.")
        writer = QPdfWriter(buffer)
        writer.setResolution(144)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter)
        writer.setCreator("FeinNote")
        writer.setTitle("FeinNote transcription")
        document = QTextDocument()
        document.documentLayout().setPaintDevice(writer)
        document.setDefaultFont(QFont("Segoe UI", 11))
        document.setDocumentMargin(0)
        options = document.defaultTextOption()
        options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(options)
        document.setPlainText(final_text(note))
        # Paginate against the printable area: otherwise Qt adds another 2 cm
        # document margin on top of the writer's configured page margins.
        document.setPageSize(QSizeF(writer.width(), writer.height()))
        document.print(writer)
        # Destruction finalizes the PDF cross-reference table before reading it.
        del writer
        result = bytes(buffer.data())
        buffer.close()
        if not result.startswith(b"%PDF-"):
            raise OSError("PDF generation failed.")
        return result
