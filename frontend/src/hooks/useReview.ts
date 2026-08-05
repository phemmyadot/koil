// Daily review chatbot React Query hooks. See
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/review";
import type { DailyReviewWithChat } from "../api/types";

export function useOnboardingStatus() {
  return useQuery({
    queryKey: ["review", "onboarding-status"],
    queryFn: api.getOnboardingStatus,
  });
}

export function useReviewDocument() {
  return useQuery({
    queryKey: ["review", "document"],
    queryFn: api.getReviewDocument,
  });
}

export function useUploadReviewDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadReviewDocument(file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["review", "document"] }),
  });
}

// Polled while the Analyzer page is open so the button state (disabled/enabled per the
// review-cycle gate) reflects real wall-clock time without a manual refresh -- same idle-poll
// pattern as useMeta(), just simpler (fixed interval, no active/idle distinction needed here).
export function useReviewStatus() {
  return useQuery({
    queryKey: ["review", "status"],
    queryFn: api.getReviewStatus,
    refetchInterval: 30_000,
  });
}

export function useTriggerDailyReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.triggerDailyReview,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["review"] }),
  });
}

export function useDailyReview(reviewDate: string | null) {
  return useQuery({
    queryKey: ["review", "daily", reviewDate],
    queryFn: () => api.getDailyReview(reviewDate as string),
    enabled: !!reviewDate,
  });
}

export function useSendReviewChatMessage(reviewDate: string) {
  const queryClient = useQueryClient();
  const queryKey = ["review", "daily", reviewDate];
  return useMutation({
    mutationFn: (message: string) => api.sendReviewChatMessage(reviewDate, message),
    // Appends the user's message to the cache immediately, before the (potentially slow)
    // Claude round-trip resolves, so the send doesn't appear to silently do nothing.
    onMutate: (message: string) => {
      queryClient.setQueryData<DailyReviewWithChat | undefined>(queryKey, (prev) =>
        prev
          ? {
              ...prev,
              chat_messages: [
                ...prev.chat_messages,
                { role: "user", content: message, created_at: new Date().toISOString() },
              ],
            }
          : prev,
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      queryClient.invalidateQueries({ queryKey: ["review", "status"] });
    },
    // The optimistic user message stays even on error -- the backend did receive and persist
    // it (see app.py's chat handler) unless the request never landed at all; invalidating
    // re-syncs with the server's real state either way.
    onError: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });
}
