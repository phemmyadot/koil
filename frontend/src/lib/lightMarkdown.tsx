// Minimal, dependency-free markdown-to-JSX renderer for the daily review chatbot's Claude
// output (backend/review_claude.py) -- covers exactly what that prompt's output actually uses
// (bold, ## headers, - bullets, paragraphs), not a general markdown parser. No new npm
// dependency for one feature's chat/review text.
import type { ReactNode } from "react";

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

// A markdown table row: | a | b | c | -- at least one interior pipe, ignoring escaped \|.
function isTableRow(line: string): boolean {
  return /^\|.*\|$/.test(line);
}

// The separator row under a table header, e.g. |---|---|---| or |:--|--:|
function isTableSeparator(line: string): boolean {
  return /^\|(\s*:?-+:?\s*\|)+$/.test(line);
}

function splitTableRow(line: string): string[] {
  return line
    .slice(1, -1)
    .split("|")
    .map((cell) => cell.trim());
}

export function LightMarkdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];

  function flushList(key: string) {
    if (listItems.length) {
      blocks.push(
        <ul key={key}>
          {listItems.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
      listItems = [];
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    if (isTableRow(trimmed) && isTableSeparator((lines[i + 1] ?? "").trim())) {
      flushList(`ul-${i}`);
      const header = splitTableRow(trimmed);
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && isTableRow(lines[j].trim())) {
        rows.push(splitTableRow(lines[j].trim()));
        j++;
      }
      blocks.push(
        <table key={i}>
          <thead>
            <tr>
              {header.map((cell, ci) => (
                <th key={ci}>{renderInline(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci}>{renderInline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      i = j - 1;
      continue;
    }

    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      listItems.push(trimmed.slice(2));
      continue;
    }
    flushList(`ul-${i}`);
    if (trimmed.startsWith("## ")) {
      blocks.push(<h3 key={i}>{renderInline(trimmed.slice(3))}</h3>);
    } else if (trimmed.startsWith("# ")) {
      blocks.push(<h2 key={i}>{renderInline(trimmed.slice(2))}</h2>);
    } else if (trimmed) {
      blocks.push(<p key={i}>{renderInline(trimmed)}</p>);
    }
  }
  flushList("ul-end");

  return <>{blocks}</>;
}
