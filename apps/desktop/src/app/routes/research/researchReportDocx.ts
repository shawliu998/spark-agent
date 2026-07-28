import JSZip from "jszip";

const WORD_NS =
  "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

function xmlEscape(value: string): string {
  const validXmlText = Array.from(value)
    .filter((character) => {
      const code = character.charCodeAt(0);
      return code === 9 || code === 10 || code === 13 || code >= 32;
    })
    .join("");
  return validXmlText
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function plainMarkdown(value: string): string {
  return value
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/~~(.*?)~~/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

function paragraphXml(
  text: string,
  style?: "Title" | "Heading1" | "Heading2" | "Heading3",
  numbering?: { id: number; level?: number },
): string {
  const styleProperty = style ? `<w:pStyle w:val="${style}"/>` : "";
  const numberingProperty = numbering
    ? `<w:numPr><w:ilvl w:val="${numbering.level ?? 0}"/><w:numId w:val="${numbering.id}"/></w:numPr>`
    : "";
  const paragraphProperties =
    styleProperty || numberingProperty
      ? `<w:pPr>${styleProperty}${numberingProperty}</w:pPr>`
      : "";
  return `<w:p>${paragraphProperties}<w:r><w:t xml:space="preserve">${xmlEscape(text)}</w:t></w:r></w:p>`;
}

type ListKind = "bullet" | "decimal";

interface NumberingInstance {
  id: number;
  kind: ListKind;
  start: number;
}

function markdownParagraphs(markdown: string): {
  xml: string;
  numbering: NumberingInstance[];
} {
  const paragraphs: string[] = [];
  const numbering: NumberingInstance[] = [];
  let firstHeading = true;
  let activeList: NumberingInstance | undefined;
  let nextNumberingId = 1;

  for (const rawLine of markdown.replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.trim();
    if (!line || /^<!--.*-->$/.test(line) || /^-{3,}$/.test(line)) {
      activeList = undefined;
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      activeList = undefined;
      const level = heading[1].length;
      const style =
        firstHeading && level === 1
          ? "Title"
          : (`Heading${level}` as "Heading1" | "Heading2" | "Heading3");
      paragraphs.push(paragraphXml(plainMarkdown(heading[2]), style));
      firstHeading = false;
      continue;
    }

    if (/^\|(?:\s*:?-+:?\s*\|)+$/.test(line)) continue;
    if (line.startsWith("|") && line.endsWith("|")) {
      activeList = undefined;
      const cells = line
        .slice(1, -1)
        .split("|")
        .map((cell) => plainMarkdown(cell));
      paragraphs.push(paragraphXml(cells.join("\t")));
      continue;
    }

    const unordered = /^[-*+]\s+(.+)$/.exec(line);
    if (unordered) {
      if (activeList?.kind !== "bullet") {
        activeList = { id: nextNumberingId++, kind: "bullet", start: 1 };
        numbering.push(activeList);
      }
      paragraphs.push(
        paragraphXml(plainMarkdown(unordered[1]), undefined, {
          id: activeList.id,
        }),
      );
      continue;
    }

    const ordered = /^(\d+)[.)]\s+(.+)$/.exec(line);
    if (ordered) {
      if (activeList?.kind !== "decimal") {
        activeList = {
          id: nextNumberingId++,
          kind: "decimal",
          start: Number(ordered[1]),
        };
        numbering.push(activeList);
      }
      paragraphs.push(
        paragraphXml(plainMarkdown(ordered[2]), undefined, {
          id: activeList.id,
        }),
      );
      continue;
    }

    activeList = undefined;
    const quote = /^>\s?(.*)$/.exec(line);
    paragraphs.push(
      paragraphXml(
        quote ? `“${plainMarkdown(quote[1])}”` : plainMarkdown(line),
      ),
    );
  }

  return { xml: paragraphs.join(""), numbering };
}

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>`;

const ROOT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`;

const DOCUMENT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>`;

const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="${WORD_NS}">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos" w:eastAsia="PingFang SC"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="240"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="0F172A"/><w:sz w:val="38"/><w:szCs w:val="38"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="320" w:after="160"/><w:keepNext/></w:pPr>
    <w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="240" w:after="120"/><w:keepNext/></w:pPr>
    <w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="160" w:after="80"/><w:keepNext/></w:pPr>
    <w:rPr><w:b/><w:color w:val="1F4D78"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>
  </w:style>
</w:styles>`;

function numberingXml(instances: NumberingInstance[]): string {
  const definitions = instances
    .map(
      (instance) =>
        `<w:num w:numId="${instance.id}"><w:abstractNumId w:val="${
          instance.kind === "bullet" ? 0 : 1
        }"/>${
          instance.kind === "decimal"
            ? `<w:lvlOverride w:ilvl="0"><w:startOverride w:val="${instance.start}"/></w:lvlOverride>`
            : ""
        }</w:num>`,
    )
    .join("");
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="${WORD_NS}">
  <w:abstractNum w:abstractNumId="0">
    <w:multiLevelType w:val="singleLevel"/>
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:spacing w:after="160" w:line="280" w:lineRule="auto"/><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos" w:eastAsia="PingFang SC"/></w:rPr></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="1">
    <w:multiLevelType w:val="singleLevel"/>
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:spacing w:after="160" w:line="280" w:lineRule="auto"/><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>
  </w:abstractNum>
  ${definitions}
</w:numbering>`;
}

/** Build a small standards-compliant DOCX from the already verified Report Markdown. */
export async function buildReportDocx(markdown: string): Promise<Uint8Array> {
  const document = markdownParagraphs(markdown);
  const zip = new JSZip();
  zip.file("[Content_Types].xml", CONTENT_TYPES);
  zip.file("_rels/.rels", ROOT_RELS);
  zip.file("word/_rels/document.xml.rels", DOCUMENT_RELS);
  zip.file("word/styles.xml", STYLES);
  zip.file("word/numbering.xml", numberingXml(document.numbering));
  zip.file(
    "word/document.xml",
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="${WORD_NS}"><w:body>${document.xml}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>`,
  );
  return zip.generateAsync({
    type: "uint8array",
    compression: "DEFLATE",
    compressionOptions: { level: 6 },
  });
}
