// Daily review chatbot API client. See
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md.

import { apiGet, apiPost, apiPostFormData } from "./client";
import type {
  DailyReviewWithChat,
  DailyReview,
  ReviewChatReply,
  ReviewDocumentResponse,
  ReviewOnboardingStatus,
  ReviewStatusResponse,
  ReviewUploadResponse,
} from "./types";

export function getOnboardingStatus(): Promise<ReviewOnboardingStatus> {
  return apiGet<ReviewOnboardingStatus>("/api/review/onboarding-status");
}

export function getReviewDocument(): Promise<ReviewDocumentResponse> {
  return apiGet<ReviewDocumentResponse>("/api/review/document");
}

export function uploadReviewDocument(file: File): Promise<ReviewUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return apiPostFormData<ReviewUploadResponse>("/api/review/document", formData);
}

export function getReviewStatus(): Promise<ReviewStatusResponse> {
  return apiGet<ReviewStatusResponse>("/api/review/status");
}

export function triggerDailyReview(): Promise<DailyReview> {
  return apiPost<DailyReview>("/api/review/daily");
}

export function getDailyReview(reviewDate: string): Promise<DailyReviewWithChat> {
  return apiGet<DailyReviewWithChat>(`/api/review/daily/${reviewDate}`);
}

export function sendReviewChatMessage(reviewDate: string, message: string): Promise<ReviewChatReply> {
  return apiPost<ReviewChatReply>(`/api/review/daily/${reviewDate}/chat`, { message });
}
