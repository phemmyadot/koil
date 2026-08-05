import { useEffect, useRef, useState } from "react";
import {
  useDailyReview,
  useOnboardingStatus,
  useReviewDocument,
  useReviewStatus,
  useSendReviewChatMessage,
  useTriggerDailyReview,
  useUploadReviewDocument,
} from "../hooks/useReview";
import { LightMarkdown } from "../lib/lightMarkdown";
import "./AnalyzerPage.css";

// Daily review chatbot page. See
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md.
export function AnalyzerPage() {
  const { data: onboarding, isLoading: onboardingLoading } = useOnboardingStatus();

  if (onboardingLoading) {
    return (
      <div className="analyzer-page">
        <p className="analyzer-loading">Loading&hellip;</p>
      </div>
    );
  }

  if (onboarding && !onboarding.onboarded) {
    return <OnboardingPrompt />;
  }

  return <AnalyzerMain />;
}

// First-visit onboarding prompt (Part 1 of the design doc, "First-visit onboarding prompt") --
// upload-or-skip, once per user's lifecycle. Neither path flips DAILY_REVIEW_ONBOARDED itself;
// that's an intentional manual step (edit .env, restart) -- this just tells the user what to do
// next rather than silently proceeding as if onboarding is complete.
function OnboardingPrompt() {
  const upload = useUploadReviewDocument();
  const [choice, setChoice] = useState<"upload" | "skip" | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleFileChosen(file: File) {
    setChoice("upload");
    await upload.mutateAsync(file);
  }

  if (choice) {
    return (
      <div className="analyzer-page">
        <div className="analyzer-onboarding-card">
          <h2>{choice === "upload" ? (upload.isPending ? "Uploading…" : "Uploaded") : "Got it"}</h2>
          {choice === "upload" && upload.isError && (
            <p className="analyzer-error">Upload failed: {upload.error instanceof Error ? upload.error.message : "unknown error"}</p>
          )}
          {(choice === "skip" || (choice === "upload" && upload.isSuccess)) && (
            <p>
              Ask your operator to set <code>DAILY_REVIEW_ONBOARDED=true</code> in <code>.env</code> and restart the
              app to finish setup.
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="analyzer-page">
      <div className="analyzer-onboarding-card">
        <h1>Welcome to the Analyzer</h1>
        <p>
          Before your first daily review, you can upload a document describing your trading philosophy &mdash;
          position sizing rules, risk tolerance, known behavioral patterns &mdash; so reviews can reference it
          directly. This is optional and only asked once.
        </p>
        <div className="analyzer-onboarding-actions">
          <button type="button" className="small-btn" onClick={() => fileInputRef.current?.click()}>
            Upload philosophy document
          </button>
          <button type="button" className="small-btn" onClick={() => setChoice("skip")}>
            Start clean (no document)
          </button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.md,.txt"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFileChosen(file);
          }}
        />
      </div>
    </div>
  );
}

function AnalyzerMain() {
  const { data: status } = useReviewStatus();
  const { data: doc } = useReviewDocument();
  const trigger = useTriggerDailyReview();
  const [showUpload, setShowUpload] = useState(false);

  const activeReviewDate = status?.active_review?.review_date ?? null;

  return (
    <div className="analyzer-page">
      <header className="analyzer-header">
        <h1>Analyzer</h1>
        <button type="button" className="small-btn" onClick={() => setShowUpload((v) => !v)}>
          {doc?.document ? `Document: ${doc.document.filename}` : "No document uploaded"}
        </button>
      </header>

      {showUpload && <DocumentPanel onClose={() => setShowUpload(false)} />}

      {activeReviewDate ? (
        <ReviewAndChat reviewDate={activeReviewDate} />
      ) : (
        <TriggerCard status={status} onTrigger={() => trigger.mutate()} pending={trigger.isPending} error={trigger.error} />
      )}
    </div>
  );
}

function DocumentPanel({ onClose }: { onClose: () => void }) {
  const { data: doc } = useReviewDocument();
  const upload = useUploadReviewDocument();
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="analyzer-doc-panel">
      {doc?.document ? (
        <p>
          Current document: <strong>{doc.document.filename}</strong> (uploaded {doc.document.uploaded_at.slice(0, 10)})
        </p>
      ) : (
        <p>No document uploaded.</p>
      )}
      <div className="analyzer-onboarding-actions">
        <button
          type="button"
          className="small-btn"
          onClick={() => {
            if (!doc?.document || window.confirm("Replace the current document?")) {
              fileInputRef.current?.click();
            }
          }}
        >
          {doc?.document ? "Replace document" : "Upload document"}
        </button>
        <button type="button" className="small-btn" onClick={onClose}>
          Close
        </button>
      </div>
      {upload.isError && (
        <p className="analyzer-error">Upload failed: {upload.error instanceof Error ? upload.error.message : "unknown error"}</p>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.docx,.md,.txt"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) upload.mutate(file);
        }}
      />
    </div>
  );
}

function TriggerCard({
  status,
  onTrigger,
  pending,
  error,
}: {
  status: { can_start: boolean } | undefined;
  onTrigger: () => void;
  pending: boolean;
  error: unknown;
}) {
  const canStart = !!status?.can_start;
  return (
    <div className="analyzer-trigger-card">
      <p>
        {canStart
          ? "Today's closing data is in. Start your review."
          : "The review button is available once the market has closed and today's data is final (weekdays, after close)."}
      </p>
      <button type="button" className="small-btn analyzer-trigger-btn" disabled={!canStart || pending} onClick={onTrigger}>
        {pending ? "Generating review…" : "Start today's review"}
      </button>
      {error != null && (
        <p className="analyzer-error">
          {error instanceof Error ? error.message : "Could not start the review."}
        </p>
      )}
    </div>
  );
}

function ReviewAndChat({ reviewDate }: { reviewDate: string }) {
  const { data: review, isLoading } = useDailyReview(reviewDate);
  const sendMessage = useSendReviewChatMessage(reviewDate);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [review?.chat_messages.length]);

  if (isLoading || !review) {
    return <p className="analyzer-loading">Loading&hellip;</p>;
  }

  const locked = review.status === "locked";

  async function handleSend() {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    await sendMessage.mutateAsync(message);
  }

  return (
    <div className="analyzer-review">
      <div className="analyzer-review-summary">
        <h2>Review &mdash; {reviewDate}</h2>
        <div className="analyzer-summary-text">
          <LightMarkdown text={review.summary_text} />
        </div>
      </div>

      <div className="analyzer-chat">
        <div className="analyzer-chat-messages" ref={scrollRef}>
          {review.chat_messages.map((m, i) => (
            <div key={i} className={`analyzer-chat-msg analyzer-chat-msg-${m.role}`}>
              <span className="analyzer-chat-role">{m.role === "system" ? "Session" : m.role === "user" ? "You" : "Analyzer"}</span>
              <LightMarkdown text={m.content} />
            </div>
          ))}
        </div>
        <div className="analyzer-chat-input-row">
          <input
            type="text"
            value={draft}
            disabled={locked || sendMessage.isPending}
            placeholder={locked ? "This review's chat has ended." : "Ask a follow-up…"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
          <button type="button" className="small-btn" disabled={locked || sendMessage.isPending || !draft.trim()} onClick={handleSend}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
