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

  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      listItems.push(trimmed.slice(2));
      return;
    }
    flushList(`ul-${i}`);
    if (trimmed.startsWith("## ")) {
      blocks.push(<h3 key={i}>{renderInline(trimmed.slice(3))}</h3>);
    } else if (trimmed.startsWith("# ")) {
      blocks.push(<h2 key={i}>{renderInline(trimmed.slice(2))}</h2>);
    } else if (trimmed) {
      blocks.push(<p key={i}>{renderInline(trimmed)}</p>);
    }
  });
  flushList("ul-end");

  return <>{blocks}</>;
}
