import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { buildReportDocx } from "./researchReportDocx";

describe("buildReportDocx", () => {
  it("creates a valid OOXML package with report structure and citations", async () => {
    const bytes = await buildReportDocx(`# Research report

## Finding

Evidence-backed finding [1].

## References

1. Example source · p. 3
`);
    const zip = await JSZip.loadAsync(bytes);

    expect(zip.file("[Content_Types].xml")).not.toBeNull();
    expect(zip.file("_rels/.rels")).not.toBeNull();
    expect(zip.file("word/styles.xml")).not.toBeNull();
    expect(zip.file("word/numbering.xml")).not.toBeNull();

    const documentXml = await zip.file("word/document.xml")!.async("string");
    const numberingXml = await zip.file("word/numbering.xml")!.async("string");
    expect(documentXml).toContain('w:pStyle w:val="Title"');
    expect(documentXml).toContain('w:pStyle w:val="Heading2"');
    expect(documentXml).toContain('<w:numId w:val="1"/>');
    expect(documentXml).toContain("Evidence-backed finding [1].");
    expect(documentXml).toContain("Example source · p. 3");
    expect(documentXml).toContain(
      '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"',
    );
    expect(numberingXml).toContain('<w:numFmt w:val="decimal"/>');
    expect(numberingXml).toContain('<w:ind w:left="720" w:hanging="360"/>');
    expect(numberingXml).toContain('<w:startOverride w:val="1"/>');
  });

  it("escapes XML and keeps CJK report text readable", async () => {
    const bytes = await buildReportDocx("# 报告\n\nA & B < C [1].");
    const zip = await JSZip.loadAsync(bytes);
    const documentXml = await zip.file("word/document.xml")!.async("string");

    expect(documentXml).toContain("报告");
    expect(documentXml).toContain("A &amp; B &lt; C [1].");
    const stylesXml = await zip.file("word/styles.xml")!.async("string");
    expect(stylesXml).toContain('w:eastAsia="PingFang SC"');
  });
});
