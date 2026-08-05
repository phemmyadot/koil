"""Splits a finished markdown string into an ordered array of chunks for the frontend's
simulated streaming reveal (Streamdown renders the array's running concatenation on each tick).
Chunk boundaries are markdown units (a heading line, a bullet line, a table row, or a paragraph)
rather than fixed character counts, so a chunk boundary never lands mid-token (e.g. inside a
**bold** span or a table cell) -- each chunk Streamdown receives is a complete, safe unit to
parse, letting parseIncompleteMarkdown do its job without ever seeing a torn fragment.

Blank lines are folded onto the end of the preceding chunk (not emitted as their own chunk) so
the reveal doesn't pause on empty vertical space.
"""


def chunk_markdown_for_stream(text: str) -> list[str]:
    if not text:
        return []

    lines = text.split("\n")
    chunks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            if chunks:
                chunks[-1] += "\n" + line
            else:
                chunks.append(line)
            i += 1
            continue
        chunks.append(line)
        i += 1

    # Re-join each unit with the newline that originally followed it, except the last.
    return [chunk + "\n" if idx < len(chunks) - 1 else chunk for idx, chunk in enumerate(chunks)]
