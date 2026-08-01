import { useRef, useState } from "react";
import { Modal } from "../atoms/Modal";
import { addDaysIso, todayIsoDate, daysBetween } from "../../lib/dates";
import { fmtMoney, fmtPct, plClass } from "../../lib/format";
import {
  computeOptSummary,
  computeSpotCalc,
  optFieldsValid,
  plModelAt,
  plAtExpiry,
  plOptionPriceAt,
  plMult,
  type OptFields,
} from "../../lib/plCalc";
import { buildPayoffChart, priceFromChartX, CHART_H, CHART_W, PAD } from "../../lib/payoffChart";
import type { OptionType } from "../../lib/blackScholes";
import "./PLCalculatorModal.css";

export interface PLCalculatorModalProps {
  onClose: () => void;
}

type PlMode = "spot" | "options";

function ResultBox({ label, value, cls }: { label: string; value: React.ReactNode; cls?: string }) {
  return (
    <div className="result-box">
      <span className="rlbl">{label}</span>
      <span className={`rval ${cls ?? ""}`}>{value}</span>
    </div>
  );
}

function SpotForm() {
  const [entry, setEntry] = useState(100);
  const [target, setTarget] = useState(115);
  const [stop, setStop] = useState(93);
  const [sizeRaw, setSizeRaw] = useState("");

  const size = sizeRaw === "" ? null : parseFloat(sizeRaw);
  const result = computeSpotCalc({ entry, target, stop, size });

  return (
    <>
      <div className="form-row">
        <label>Entry Price</label>
        <input type="number" step={0.01} value={entry} onChange={(e) => setEntry(Number(e.target.value))} />
      </div>
      <div className="form-row">
        <label>Target Price</label>
        <input type="number" step={0.01} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
      </div>
      <div className="form-row">
        <label>Stop Price</label>
        <input type="number" step={0.01} value={stop} onChange={(e) => setStop(Number(e.target.value))} />
      </div>
      <div className="form-row">
        <label>Position Size ($, optional)</label>
        <input type="number" step={1} placeholder="e.g. 1000" value={sizeRaw} onChange={(e) => setSizeRaw(e.target.value)} />
      </div>
      {result && (
        <div className="result-grid">
          <ResultBox label="Max Gain" value={fmtPct(result.gainPct)} cls={plClass(result.gainPct)} />
          <ResultBox label="Max Loss" value={fmtPct(result.lossPct)} cls={plClass(result.lossPct)} />
          {result.gainDollars != null && (
            <ResultBox label="Max Gain ($)" value={fmtMoney(result.gainDollars)} cls={plClass(result.gainDollars)} />
          )}
          {result.lossDollars != null && (
            <ResultBox label="Max Loss ($)" value={fmtMoney(result.lossDollars)} cls={plClass(result.lossDollars)} />
          )}
          {result.riskReward != null && <ResultBox label="Risk:Reward" value={`1 : ${result.riskReward.toFixed(2)}`} />}
        </div>
      )}
    </>
  );
}

function ToggleGrp<T extends string>({
  options,
  value,
  onChange,
}: {
  options: [T, string][];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="toggle-grp">
      {options.map(([val, label]) => (
        <button
          key={val}
          type="button"
          className={`${val}${val === value ? " active" : ""}`}
          onClick={() => onChange(val)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function OptionsForm() {
  const today = todayIsoDate();
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<OptionType>("call");
  const [strike, setStrike] = useState(105);
  const [premium, setPremium] = useState(3.0);
  const [contracts, setContracts] = useState(1);
  const [spot, setSpot] = useState(100);
  const [iv, setIv] = useState(35);
  const [entryDate, setEntryDate] = useState(today);
  const [expiryDate, setExpiryDate] = useState(addDaysIso(today, 30));
  const [targetPriceRaw, setTargetPriceRaw] = useState("");

  // Set to null on load / whenever the entry->expiry timeline resets; both then default to
  // "spot" / "today" respectively so the chart has sane initial values without stale refs.
  const [evalPrice, setEvalPrice] = useState<number | null>(null);
  const [evalDays, setEvalDays] = useState<number | null>(null);
  const draggingRef = useRef(false);
  const chartRef = useRef<SVGSVGElement>(null);

  const dte = Math.max(daysBetween(entryDate, expiryDate), 1);
  const daysElapsed = Math.min(Math.max(daysBetween(entryDate, today), 0), dte);
  const f: OptFields = {
    side,
    type,
    K: strike,
    premium,
    contracts,
    S: spot,
    iv: Math.max(iv, 0.01) / 100,
    entryDate,
    expiryDate,
    dte,
    daysElapsed,
  };
  const valid = optFieldsValid(f);
  const effEvalPrice = evalPrice ?? f.S;
  const effEvalDays = Math.min(Math.max(evalDays ?? f.daysElapsed, 0), f.dte);
  const targetPrice = targetPriceRaw === "" ? null : parseFloat(targetPriceRaw);

  function scrubToClientX(clientX: number) {
    const svg = chartRef.current;
    if (!svg || !valid) return;
    const rect = svg.getBoundingClientRect();
    setEvalPrice(priceFromChartX(clientX, rect.left, rect.width, f));
  }
  function clientXOf(e: React.MouseEvent | React.TouchEvent): number {
    return "touches" in e ? e.touches[0].clientX : (e as React.MouseEvent).clientX;
  }

  if (!valid) {
    return (
      <>
        <OptFieldsGrid
          side={side}
          type={type}
          strike={strike}
          premium={premium}
          contracts={contracts}
          spot={spot}
          iv={iv}
          entryDate={entryDate}
          expiryDate={expiryDate}
          targetPriceRaw={targetPriceRaw}
          setSide={setSide}
          setType={setType}
          setStrike={setStrike}
          setPremium={setPremium}
          setContracts={setContracts}
          setSpot={(v) => {
            setSpot(v);
            setEvalPrice(v);
          }}
          setIv={setIv}
          setEntryDate={(v) => {
            setEntryDate(v);
            setEvalDays(null);
          }}
          setExpiryDate={(v) => {
            setExpiryDate(v);
            setEvalDays(null);
          }}
          setTargetPriceRaw={setTargetPriceRaw}
        />
      </>
    );
  }

  const evalPL = plModelAt(f, effEvalPrice, effEvalDays);
  const evalPLExp = plAtExpiry(f, effEvalPrice);
  const evalOptPrice = plOptionPriceAt(f, effEvalPrice, effEvalDays);
  const cost = f.premium * plMult(f);
  const evalRet = cost > 0 ? (evalPL / cost) * 100 : 0;
  const summary = computeOptSummary(f);
  const breakeven = summary.breakeven;
  const chart = buildPayoffChart(f, effEvalDays, effEvalPrice, breakeven);

  return (
    <>
      <OptFieldsGrid
        side={side}
        type={type}
        strike={strike}
        premium={premium}
        contracts={contracts}
        spot={spot}
        iv={iv}
        entryDate={entryDate}
        expiryDate={expiryDate}
        targetPriceRaw={targetPriceRaw}
        setSide={setSide}
        setType={setType}
        setStrike={setStrike}
        setPremium={setPremium}
        setContracts={setContracts}
        setSpot={(v) => {
          setSpot(v);
          setEvalPrice(v);
        }}
        setIv={setIv}
        setEntryDate={(v) => {
          setEntryDate(v);
          setEvalDays(null);
        }}
        setExpiryDate={(v) => {
          setExpiryDate(v);
          setEvalDays(null);
        }}
        setTargetPriceRaw={setTargetPriceRaw}
      />
      <svg
        ref={chartRef}
        className="payoff-chart"
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        onMouseDown={(e) => {
          e.preventDefault();
          draggingRef.current = true;
          scrubToClientX(clientXOf(e));
        }}
        onMouseMove={(e) => {
          if (draggingRef.current) scrubToClientX(clientXOf(e));
        }}
        onMouseUp={() => (draggingRef.current = false)}
        onMouseLeave={() => (draggingRef.current = false)}
        onTouchStart={(e) => {
          draggingRef.current = true;
          scrubToClientX(clientXOf(e));
        }}
        onTouchMove={(e) => {
          if (draggingRef.current) scrubToClientX(clientXOf(e));
        }}
        onTouchEnd={() => (draggingRef.current = false)}
      >
        {chart.yTicks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={PAD.l + (CHART_W - PAD.l - PAD.r)} y1={t.y} y2={t.y} stroke="var(--line)" strokeWidth={1} />
            <text className="axis-label" x={PAD.l - 6} y={t.y + 3} textAnchor="end">
              {t.label}
            </text>
          </g>
        ))}
        {chart.xTicks.map((t, i) => (
          <text key={i} className="axis-label" x={t.x} y={CHART_H - 8} textAnchor="middle">
            {t.label}
          </text>
        ))}
        <line
          x1={PAD.l}
          x2={PAD.l + (CHART_W - PAD.l - PAD.r)}
          y1={chart.zeroY}
          y2={chart.zeroY}
          stroke="var(--muted)"
          strokeWidth={1.2}
          opacity={0.7}
        />
        <path d={chart.expPath} fill="none" stroke="var(--text)" strokeWidth={2} opacity={0.9} />
        <path
          d={chart.modelPath}
          fill="none"
          stroke="var(--gold)"
          strokeWidth={1.6}
          strokeDasharray="5,4"
          opacity={0.95}
        />
        <line
          x1={chart.strikeX}
          x2={chart.strikeX}
          y1={PAD.t}
          y2={PAD.t + (CHART_H - PAD.t - PAD.b)}
          stroke="var(--muted)"
          strokeWidth={1}
          strokeDasharray="2,4"
        />
        <text className="mark-label" x={chart.strikeX + 4} y={PAD.t + 11}>
          {chart.strikeLabel}
        </text>
        {chart.breakevenPoint && (
          <>
            <circle
              cx={chart.breakevenPoint.x}
              cy={chart.breakevenPoint.y}
              r={3.5}
              fill="var(--panel)"
              stroke="var(--gold)"
              strokeWidth={2}
            />
            <text className="mark-label" x={chart.breakevenPoint.x} y={chart.breakevenPoint.y - 8} textAnchor="middle" fill="var(--gold)">
              {chart.breakevenLabel}
            </text>
          </>
        )}
        <line
          x1={chart.evalPoint.x}
          x2={chart.evalPoint.x}
          y1={PAD.t}
          y2={PAD.t + (CHART_H - PAD.t - PAD.b)}
          stroke={chart.evalProfit ? "var(--green)" : "var(--red)"}
          strokeWidth={1}
          opacity={0.5}
        />
        <circle
          cx={chart.evalPoint.x}
          cy={chart.evalPoint.y}
          r={5}
          fill={chart.evalProfit ? "var(--green)" : "var(--red)"}
          stroke="var(--panel)"
          strokeWidth={2}
        />
      </svg>
      <div className="pl-legend">
        <span>&mdash; At expiration</span>
        <span style={{ color: "var(--gold)" }}>- - Model (Black-Scholes)</span>
        <span>Drag chart to scrub price</span>
      </div>
      <div className="form-row" style={{ marginTop: 8 }}>
        <label>
          Time forward: Day {effEvalDays} of {f.dte} (today = Day {f.daysElapsed}, expires {f.expiryDate})
        </label>
      </div>
      <input
        type="range"
        min={0}
        max={f.dte}
        step={1}
        value={effEvalDays}
        onChange={(e) => setEvalDays(Number(e.target.value))}
        style={{ width: "100%" }}
      />
      <div className="result-grid">
        <ResultBox label={effEvalDays === 0 ? "Stock at today" : `Stock at +${effEvalDays}d`} value={fmtMoney(effEvalPrice)} />
        <ResultBox label="Option price (model)" value={fmtMoney(evalOptPrice)} />
        <ResultBox label="P/L (model)" value={`${fmtMoney(evalPL)} (${fmtPct(evalRet)})`} cls={plClass(evalPL)} />
        <ResultBox label="P/L at expiry" value={fmtMoney(evalPLExp)} cls={plClass(evalPLExp)} />
        {targetPrice != null && (
          <>
            <ResultBox label={`Option @ target $${targetPrice} (now)`} value={fmtMoney(plOptionPriceAt(f, targetPrice, effEvalDays))} />
            <ResultBox
              label={`Option @ target $${targetPrice} (expiry)`}
              value={fmtMoney(f.type === "call" ? Math.max(targetPrice - f.K, 0) : Math.max(f.K - targetPrice, 0))}
            />
          </>
        )}
      </div>
      <div className="result-grid" style={{ marginTop: 6 }}>
        <ResultBox label={side === "buy" ? "Cost (max risk)" : "Credit received"} value={fmtMoney(summary.cost)} />
        <ResultBox label="Breakeven" value={fmtMoney(summary.breakeven)} />
        <ResultBox label="Max Profit" value={summary.maxProfit} cls="pos" />
        <ResultBox label="Max Loss" value={summary.maxLoss} cls="neg" />
      </div>
      <p className="pl-note">
        Model curve uses Black-Scholes with constant IV and a 4.5% risk-free rate &mdash; an estimate, not a quote.
        Educational tool only, not financial advice.
      </p>
    </>
  );
}

function OptFieldsGrid(props: {
  side: "buy" | "sell";
  type: OptionType;
  strike: number;
  premium: number;
  contracts: number;
  spot: number;
  iv: number;
  entryDate: string;
  expiryDate: string;
  targetPriceRaw: string;
  setSide: (v: "buy" | "sell") => void;
  setType: (v: OptionType) => void;
  setStrike: (v: number) => void;
  setPremium: (v: number) => void;
  setContracts: (v: number) => void;
  setSpot: (v: number) => void;
  setIv: (v: number) => void;
  setEntryDate: (v: string) => void;
  setExpiryDate: (v: string) => void;
  setTargetPriceRaw: (v: string) => void;
}) {
  return (
    <>
      <div className="opt-grid2">
        <ToggleGrp
          options={[
            ["buy", "Buy"],
            ["sell", "Sell"],
          ]}
          value={props.side}
          onChange={props.setSide}
        />
        <ToggleGrp
          options={[
            ["call", "Call"],
            ["put", "Put"],
          ]}
          value={props.type}
          onChange={props.setType}
        />
      </div>
      <div className="opt-fields">
        <div className="form-row">
          <label>Strike</label>
          <input type="number" step={1} value={props.strike} onChange={(e) => props.setStrike(Number(e.target.value))} />
        </div>
        <div className="form-row">
          <label>Premium ($/sh)</label>
          <input type="number" step={0.05} value={props.premium} onChange={(e) => props.setPremium(Number(e.target.value))} />
        </div>
        <div className="form-row">
          <label>Contracts</label>
          <input type="number" step={1} value={props.contracts} onChange={(e) => props.setContracts(Number(e.target.value))} />
        </div>
        <div className="form-row">
          <label>Current Price</label>
          <input type="number" step={0.5} value={props.spot} onChange={(e) => props.setSpot(Number(e.target.value))} />
        </div>
        <div className="form-row">
          <label>Implied Vol (%)</label>
          <input type="number" step={0.5} value={props.iv} onChange={(e) => props.setIv(Number(e.target.value))} />
        </div>
        <div className="form-row">
          <label>Entry Date</label>
          <input type="date" value={props.entryDate} onChange={(e) => props.setEntryDate(e.target.value)} />
        </div>
        <div className="form-row">
          <label>Expiration Date</label>
          <input type="date" value={props.expiryDate} onChange={(e) => props.setExpiryDate(e.target.value)} />
        </div>
        <div className="form-row">
          <label>Target Price (optional)</label>
          <input
            type="number"
            step={0.5}
            placeholder="e.g. 40"
            value={props.targetPriceRaw}
            onChange={(e) => props.setTargetPriceRaw(e.target.value)}
          />
        </div>
      </div>
    </>
  );
}

export function PLCalculatorModal({ onClose }: PLCalculatorModalProps) {
  const [mode, setMode] = useState<PlMode>("spot");
  return (
    <Modal title="P/L Calculator" onClose={onClose} width={mode === "options" ? 620 : 480}>
      <div className="plcalc">
        <div className="mode-mid">
          <button type="button" className={`mode-btn${mode === "spot" ? " active" : ""}`} onClick={() => setMode("spot")}>
            Spot
          </button>
          <button type="button" className={`mode-btn${mode === "options" ? " active" : ""}`} onClick={() => setMode("options")}>
            Options
          </button>
        </div>
        {mode === "spot" ? <SpotForm /> : <OptionsForm key="options" />}
      </div>
    </Modal>
  );
}
