// Daily review chatbot React Query hooks. See
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/review";

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
  return useMutation({
    mutationFn: (message: string) => api.sendReviewChatMessage(reviewDate, message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review", "daily", reviewDate] });
      queryClient.invalidateQueries({ queryKey: ["review", "status"] });
    },
  });
}
