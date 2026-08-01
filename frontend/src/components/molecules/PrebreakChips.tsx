import { Chip } from "../atoms/Chip";
import type { PrebreakResult } from "../../api/types";
import type { ColorTier } from "../../lib/colorTiers";

// Mirrors pre-break.pine's own HUD table rows: Market Phase Score / Volatility Squeeze /
// Volume Dry-up / Clustering At Ceiling / Trend Filter / Coil Energy Build-up.
const PREBREAK_STATE_CLASS: Record<string, ColorTier> = {
  BEARISH: "no",
  NEUTRAL: "neutral",
  BULLISH: "mid",
  "COILING (BULL)": "mid",
  "PRE-BREAKOUT": "mid",
  BREAKOUT: "ok",
};

export function PrebreakChips({ pb }: { pb: PrebreakResult }) {
  const stateCls = PREBREAK_STATE_CLASS[pb.state] ?? "neutral";
  const stateTitle =
    pb.projected_target != null
      ? `Target ${pb.projected_target} (~${pb.projected_duration}b)`
      : pb.state;
  return (
    <div className="condgrid">
      <Chip tone={stateCls}>
        <span title={stateTitle}>
          {pb.state} ({pb.score})
        </span>
      </Chip>
      <Chip tone={pb.bb_squeeze ? "mid" : "ok"}>
        <span title="Volatility Squeeze">{pb.bb_squeeze ? "COMPRESSED" : "EXPANDED"}</span>
      </Chip>
      <Chip tone={pb.vol_dry_up ? "mid" : "ok"}>
        <span title="Volume Dry-up">{pb.vol_dry_up ? "DRY" : "NORMAL/HIGH"}</span>
      </Chip>
      <Chip tone={pb.near_resistance ? "mid" : "ok"}>
        <span title="Clustering At Ceiling">{pb.near_resistance ? "COILING" : "CLEAR"}</span>
      </Chip>
      <Chip tone={pb.is_bullish_trend ? "mid" : "no"}>
        <span title="Trend Filter">{pb.is_bullish_trend ? "BULLISH" : "BEARISH"}</span>
      </Chip>
      <Chip tone={pb.squeeze_counter > 30 ? "active" : "neutral"}>
        <span title="Coil Energy Build-up">{pb.squeeze_counter} Bars</span>
      </Chip>
    </div>
  );
}
