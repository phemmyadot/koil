import { useEffect, useRef, useState } from "react";
import { Streamdown } from "streamdown";

// Simulated streaming reveal: chunks (backend/review_stream.py's markdown-unit split) are
// already the complete final text -- this just reveals them progressively so a long review
// doesn't dump on screen all at once. Streamdown handles any mid-reveal partial markdown
// safely (parseIncompleteMarkdown, on by default), though chunk boundaries are already whole
// lines so that rarely matters in practice.
const CHUNK_INTERVAL_MS = 60;

export function StreamingReview({
  chunks,
  onDone,
  onProgress,
}: {
  chunks: string[];
  onDone?: () => void;
  onProgress?: () => void;
}) {
  const [visibleCount, setVisibleCount] = useState(chunks.length > 0 ? 1 : 0);
  const chunksRef = useRef(chunks);
  chunksRef.current = chunks;

  useEffect(() => {
    setVisibleCount(chunks.length > 0 ? 1 : 0);
  }, [chunks]);

  // Fires on every revealed chunk (including the first) so the caller can keep a scroll
  // container pinned to the bottom as the content grows, not just once at the start/end.
  useEffect(() => {
    onProgress?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleCount]);

  useEffect(() => {
    if (visibleCount >= chunksRef.current.length) {
      if (chunksRef.current.length > 0 && visibleCount === chunksRef.current.length) {
        onDone?.();
      }
      return;
    }
    const id = setTimeout(() => setVisibleCount((n) => n + 1), CHUNK_INTERVAL_MS);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleCount]);

  const text = chunks.slice(0, visibleCount).join("");
  return <Streamdown>{text}</Streamdown>;
}
