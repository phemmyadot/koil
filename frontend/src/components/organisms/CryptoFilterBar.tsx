import { useState } from "react";
import { FilterPopover } from "../molecules/FilterPopover";
import { PF_STEPS, WR_STEPS } from "../../constants/filterDefaults";
import "./FilterBar.css";

export interface CryptoFilterBarState {
  tickerSearch: string;
  minTrades: number;
  wrMin: number;
  pfMin: number;
}

export function defaultCryptoFilterBarState(): CryptoFilterBarState {
  return { tickerSearch: "", minTrades: 15, wrMin: 70, pfMin: 2.0 };
}

function sliderSteps(steps: readonly (string | number)[], suffix = "") {
  return steps.map((v, i) => <span key={i}>{v}{suffix}</span>);
}

export interface CryptoFilterBarProps {
  state: CryptoFilterBarState;
  onChange: (next: CryptoFilterBarState) => void;
}

// Subset of equity's FilterBar: ticker search + min trades + WR/PF sliders only -- crypto has
// one strategy (strategy_vcp), so there's no strategy radio, "trade on" selector, or
// pre-breakout section to mirror.
export function CryptoFilterBar({ state, onChange }: CryptoFilterBarProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [advOpen, setAdvOpen] = useState(false);
  const advActive = (state.wrMin > WR_STEPS[0] ? 1 : 0) + (state.pfMin > PF_STEPS[0] ? 1 : 0);

  return (
    <div className="filterbar">
      <button
        type="button"
        className="filtertoggle"
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen((v) => !v)}
      >
        Filters <span className="filtertogglearrow">&#9662;</span>
      </button>
      <div className={`filtercontrols${mobileOpen ? " open" : ""}`}>
        <div className="filterrow1">
          <input
            type="text"
            placeholder="Search ticker&hellip;"
            autoComplete="off"
            maxLength={10}
            value={state.tickerSearch}
            onChange={(e) => onChange({ ...state, tickerSearch: e.target.value })}
          />
          <span className="sep" />
          <label htmlFor="cryptoMinTradesFilter">Min trades</label>
          <input
            id="cryptoMinTradesFilter"
            type="number"
            min={0}
            step={1}
            value={state.minTrades}
            onChange={(e) => onChange({ ...state, minTrades: Number(e.target.value) || 0 })}
          />
        </div>

        <span className="sep" />

        <FilterPopover
          label="Advance Filter"
          activeCount={advActive}
          onClear={() => onChange({ ...state, wrMin: WR_STEPS[0], pfMin: PF_STEPS[0] })}
          open={advOpen}
          onOpenChange={setAdvOpen}
          className="filterrow2"
        >
          <div className="advsliderrow">
            <div className="advslider">
              <div className="advsectionlabel">Win Rate</div>
              <input
                type="range"
                min={0}
                max={WR_STEPS.length - 1}
                step={1}
                value={WR_STEPS.indexOf(state.wrMin as (typeof WR_STEPS)[number])}
                onChange={(e) => onChange({ ...state, wrMin: WR_STEPS[Number(e.target.value)] })}
              />
              <div className="advslidersteps">{sliderSteps(WR_STEPS, "%")}</div>
            </div>
            <span className="advand">AND</span>
            <div className="advslider">
              <div className="advsectionlabel">Profit</div>
              <input
                type="range"
                min={0}
                max={PF_STEPS.length - 1}
                step={1}
                value={PF_STEPS.indexOf(state.pfMin as (typeof PF_STEPS)[number])}
                onChange={(e) => onChange({ ...state, pfMin: PF_STEPS[Number(e.target.value)] })}
              />
              <div className="advslidersteps">{sliderSteps(PF_STEPS)}</div>
            </div>
          </div>
        </FilterPopover>

        <span className="sep" />
        <button type="button" className="clearfilters" onClick={() => onChange(defaultCryptoFilterBarState())}>
          Clear filters
        </button>
      </div>
    </div>
  );
}
