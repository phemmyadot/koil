import type { StrategyKey, StrategyResult, TickerPayload } from "../../api/types";
import { ADV_STRAT_KEY, type StrategyShortKey } from "../../constants/strategy";
import { StrategyBadgeRow } from "../molecules/StrategyBadgeRow";
import { PrebreakChips } from "../molecules/PrebreakChips";
import { scoreColor } from "../../lib/scoreColor";
import "./TickerCard.css";

const STRATEGY_ROWS: [StrategyKey, string][] = [
  ["vexh", "VEXH"],
  ["strategy_vcp", "VCP"],
  ["strategy_vcpo", "VCPO"],
];

export interface TickerCardProps {
  row: TickerPayload;
  scoreStrategy: StrategyShortKey;
  selected: boolean;
  onToggleSelect: (ticker: string) => void;
  onOpenStrategy: (ticker: string, stratKey: StrategyKey) => void;
}

function StrategyCell({
  stratKey,
  s,
  ticker,
  onOpenStrategy,
}: {
  stratKey: StrategyKey;
  s: StrategyResult | null;
  ticker: string;
  onOpenStrategy: (ticker: string, stratKey: StrategyKey) => void;
}) {
  if (!s) {
    return <span className="chip chip-neutral">&mdash;</span>;
  }
  const daysHeld = s.open_position ? s.open_position.days_held : null;
  const pending = !!(s.signal_today && !s.open_position);
  return (
    <StrategyBadgeRow
      active={!!s.open_position}
      daysHeld={daysHeld}
      pending={pending}
      nTrades={s.n_trades}
      winRate={s.win_rate}
      profitFactor={s.profit_factor}
      onClick={() => onOpenStrategy(ticker, stratKey)}
    />
  );
}

// Replaces one card in the #rows grid (index.html).
export function TickerCard({ row, scoreStrategy, selected, onToggleSelect, onOpenStrategy }: TickerCardProps) {
  const cardScore = row.setup_score?.[ADV_STRAT_KEY[scoreStrategy]] ?? null;
  const isFire = cardScore != null && cardScore >= 9 && !row.earnings_risk;

  return (
    <div className={`tickercard ${isFire ? "fire" : ""}`}>
      <div className="cardhead">
        <a
          className="tklink tk"
          href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(row.ticker)}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          {row.ticker}
        </a>
        <span className="price num">${row.price.toFixed(2)}</span>
        {cardScore != null && (
          <span
            className="qscore"
            style={{ color: scoreColor(cardScore) }}
            title={`Setup quality score (0-10), ${scoreStrategy.toUpperCase()}`}
          >
            {cardScore}/10
          </span>
        )}
        {cardScore != null &&
          cardScore >= 9 &&
          (row.earnings_risk ? (
            <span
              className="firetag weak"
              title="earnings report within the entry-avoid window -- engine will not take this entry (validated: holding through earnings drops win rate 62%->51%)"
            >
              ENTRY (earnings risk)
            </span>
          ) : (
            <span className="firetag">ENTRY</span>
          ))}
        {row.days_to_earnings != null && row.days_to_earnings <= 21 && row.days_to_earnings >= 0 && (
          <span
            className={`earningsbadge ${row.days_to_earnings <= 5 ? "soon" : ""}`}
            title="days until this ticker's next known earnings report"
          >
            Earnings {row.days_to_earnings === 0 ? "0D" : `+${row.days_to_earnings}D`}
          </span>
        )}
        <input
          type="checkbox"
          className="cardcheck"
          checked={selected}
          onChange={() => onToggleSelect(row.ticker)}
          title="Select for watchlist"
        />
      </div>
      {row.prebreak && (
        <div className="cardsection">
          <span className="lbl">Pre-Breakout</span>
          <PrebreakChips pb={row.prebreak} />
        </div>
      )}
      <hr className="divider" />
      <div className="scoresections">
        <div className="scorebox">
          <div className="strategygrid2">
            {STRATEGY_ROWS.map(([key, label]) => (
              <div className="strategycell" key={key}>
                <span className="strategyname">{label}</span>
                <div className="strategybody">
                  <StrategyCell stratKey={key} s={row[key]} ticker={row.ticker} onOpenStrategy={onOpenStrategy} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
