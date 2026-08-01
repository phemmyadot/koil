import { Chip } from "../atoms/Chip";
import { pfColorClass, tradeCountColorClass, wrColorClass } from "../../lib/colorTiers";
import "./StrategyBadgeRow.css";

export interface StrategyBadgeRowProps {
  active: boolean;
  daysHeld: number | null;
  pending: boolean;
  nTrades: number;
  winRate: number;
  profitFactor: number;
  onClick: () => void;
}

// Replaces statBadges/statBadgesHtml (index.html + watchlist.html, previously 2 slightly
// different signatures) with one shared component.
export function StrategyBadgeRow({
  active,
  daysHeld,
  pending,
  nTrades,
  winRate,
  profitFactor,
  onClick,
}: StrategyBadgeRowProps) {
  // Win count derived from win_rate% and n_trades -- backend only stores the percentage.
  const wins = Math.round((winRate / 100) * nTrades);
  return (
    <span className="badge-row">
      {active && (
        <Chip tone="active" onClick={onClick}>
          Open -{daysHeld}D
        </Chip>
      )}
      {pending && (
        <Chip tone="pending" onClick={onClick}>
          PENDING
        </Chip>
      )}
      <Chip tone={tradeCountColorClass(nTrades)} onClick={onClick}>
        T {nTrades}
      </Chip>
      <Chip tone={wrColorClass(winRate)} onClick={onClick}>
        WR {winRate.toFixed(2)}%({wins}/{nTrades})
      </Chip>
      <Chip tone={pfColorClass(profitFactor)} onClick={onClick}>
        PF {profitFactor.toFixed(2)}
      </Chip>
    </span>
  );
}
