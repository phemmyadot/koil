import { useState } from "react";
import { FilterPopover } from "../molecules/FilterPopover";
import {
  ADV_PF_DEFAULT_INDEX,
  ADV_STRATEGY_DEFAULT,
  ADV_WR_DEFAULT_INDEX,
  COIL_DEFAULT_INDEX,
  COIL_STEPS,
  PF_STEPS,
  PHASE_DEFAULT_INDEX,
  PHASE_STEPS,
  PREBREAK_SWITCHES,
  WR_STEPS,
} from "../../constants/filterDefaults";
import { ADV_STRATEGIES, type StrategyShortKey } from "../../constants/strategy";
import type { AdvFilterState, PrebreakFilterState } from "../../lib/filters";
import type { FilterDefaultsResponse } from "../../api/types";
import "./FilterBar.css";

export interface FilterBarState {
  tickerSearch: string;
  minTrades: number;
  adv: AdvFilterState;
  tradeOnStrats: StrategyShortKey[];
  prebreak: PrebreakFilterState;
}

export const MIN_TRADES_DEFAULT = 15;

export function defaultFilterBarState(): FilterBarState {
  return {
    tickerSearch: "",
    minTrades: MIN_TRADES_DEFAULT,
    adv: { strategy: ADV_STRATEGY_DEFAULT, wrMin: WR_STEPS[ADV_WR_DEFAULT_INDEX], pfMin: PF_STEPS[ADV_PF_DEFAULT_INDEX] },
    tradeOnStrats: [],
    prebreak: { phaseMin: PHASE_STEPS[PHASE_DEFAULT_INDEX][1], coilMin: COIL_STEPS[COIL_DEFAULT_INDEX], switches: {} },
  };
}

// Builds FilterBarState from backend/quality_filter.py's DEFAULT_FILTER.
export function filterBarStateFromDefaults(defaults: FilterDefaultsResponse): FilterBarState {
  const strategy = (defaults.strategies[0] ?? ADV_STRATEGY_DEFAULT) as StrategyShortKey;
  return {
    tickerSearch: "",
    minTrades: defaults.min_trades,
    adv: { strategy, wrMin: defaults.min_win_rate, pfMin: defaults.min_profit_factor },
    tradeOnStrats: [],
    prebreak: { phaseMin: defaults.min_phase_score, coilMin: defaults.min_coil_bars, switches: {} },
  };
}

function sliderSteps(steps: readonly (string | number)[], suffix = "") {
  return steps.map((v, i) => <span key={i}>{v}{suffix}</span>);
}

function advWrIndex(wrMin: number) {
  const idx = WR_STEPS.indexOf(wrMin as (typeof WR_STEPS)[number]);
  return idx >= 0 ? idx : ADV_WR_DEFAULT_INDEX;
}
function advPfIndex(pfMin: number) {
  const idx = PF_STEPS.indexOf(pfMin as (typeof PF_STEPS)[number]);
  return idx >= 0 ? idx : ADV_PF_DEFAULT_INDEX;
}
function phaseIndex(phaseMin: number) {
  const idx = PHASE_STEPS.findIndex(([, v]) => v === phaseMin);
  return idx >= 0 ? idx : PHASE_DEFAULT_INDEX;
}
function coilIndex(coilMin: number) {
  const idx = COIL_STEPS.indexOf(coilMin as (typeof COIL_STEPS)[number]);
  return idx >= 0 ? idx : COIL_DEFAULT_INDEX;
}

export interface FilterBarProps {
  state: FilterBarState;
  onChange: (next: FilterBarState) => void;
}

// Replaces the filterbar + its 3 popover panels (index.html). Panel visibility/outside-click is
// owned by FilterPopover; this component only owns the filter values themselves.
type PopoverKey = "adv" | "tradeOn" | "prebreak";

export function FilterBar({ state, onChange }: FilterBarProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  // Only one popover open at a time -- opening one closes whichever else was open.
  const [openPopover, setOpenPopover] = useState<PopoverKey | null>(null);
  const advActive = (state.adv.wrMin > WR_STEPS[0] ? 1 : 0) + (state.adv.pfMin > PF_STEPS[0] ? 1 : 0);
  const prebreakActive =
    (state.prebreak.phaseMin !== PHASE_STEPS[PHASE_DEFAULT_INDEX][1] ? 1 : 0) +
    (state.prebreak.coilMin > COIL_STEPS[0] ? 1 : 0) +
    Object.values(state.prebreak.switches).filter(Boolean).length;

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
          maxLength={6}
          value={state.tickerSearch}
          onChange={(e) => onChange({ ...state, tickerSearch: e.target.value })}
        />
        <span className="sep" />
        <label htmlFor="minTradesFilter">Min trades</label>
        <input
          id="minTradesFilter"
          type="number"
          min={0}
          step={1}
          value={state.minTrades}
          onChange={(e) => onChange({ ...state, minTrades: Number(e.target.value) || 0 })}
        />
        <span className="sep" />

        <FilterPopover
          label="Trade on"
          activeCount={state.tradeOnStrats.length}
          onClear={() => onChange({ ...state, tradeOnStrats: [] })}
          open={openPopover === "tradeOn"}
          onOpenChange={(open) => setOpenPopover(open ? "tradeOn" : null)}
        >
          <div className="advmetric">
            {ADV_STRATEGIES.map(([key, label]) => (
              <label className="advcheck" key={key}>
                <input
                  type="checkbox"
                  checked={state.tradeOnStrats.includes(key)}
                  onChange={(e) =>
                    onChange({
                      ...state,
                      tradeOnStrats: e.target.checked
                        ? [...state.tradeOnStrats, key]
                        : state.tradeOnStrats.filter((k) => k !== key),
                    })
                  }
                />
                {label}
              </label>
            ))}
          </div>
        </FilterPopover>
      </div>

      <span className="sep" />

      <FilterPopover
        label="Advance Filter"
        activeCount={advActive}
        onClear={() => onChange({ ...state, adv: defaultFilterBarState().adv })}
        open={openPopover === "adv"}
        onOpenChange={(open) => setOpenPopover(open ? "adv" : null)}
        className="filterrow2"
      >
        <div className="advsectionlabel">Strategy</div>
        <div className="advradiorow">
          {ADV_STRATEGIES.map(([key, label]) => (
            <label className="advcheck" key={key}>
              <input
                type="radio"
                name="advStrategy"
                checked={state.adv.strategy === key}
                onChange={() => onChange({ ...state, adv: { ...state.adv, strategy: key } })}
              />
              {label}
            </label>
          ))}
        </div>
        <div className="advsliderrow">
          <div className="advslider">
            <div className="advsectionlabel">Win Rate</div>
            <input
              type="range"
              min={0}
              max={WR_STEPS.length - 1}
              step={1}
              value={advWrIndex(state.adv.wrMin)}
              onChange={(e) => onChange({ ...state, adv: { ...state.adv, wrMin: WR_STEPS[Number(e.target.value)] } })}
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
              value={advPfIndex(state.adv.pfMin)}
              onChange={(e) => onChange({ ...state, adv: { ...state.adv, pfMin: PF_STEPS[Number(e.target.value)] } })}
            />
            <div className="advslidersteps">{sliderSteps(PF_STEPS)}</div>
          </div>
        </div>
      </FilterPopover>

      <span className="sep" />

      <FilterPopover
        label="Pre-Breakout"
        activeCount={prebreakActive}
        onClear={() => onChange({ ...state, prebreak: defaultFilterBarState().prebreak })}
        open={openPopover === "prebreak"}
        onOpenChange={(open) => setOpenPopover(open ? "prebreak" : null)}
        className="filterrow3"
      >
        <div className="advsectionlabel">Phase</div>
        <input
          type="range"
          min={0}
          max={PHASE_STEPS.length - 1}
          step={1}
          value={phaseIndex(state.prebreak.phaseMin)}
          onChange={(e) =>
            onChange({ ...state, prebreak: { ...state.prebreak, phaseMin: PHASE_STEPS[Number(e.target.value)][1] } })
          }
        />
        <div className="advslidersteps">{sliderSteps(PHASE_STEPS.map(([name]) => name))}</div>
        <div className="switchgrid">
          {PREBREAK_SWITCHES.map(([key, label]) => (
            <label className="switchrow" key={key}>
              {label}
              <span className="switch">
                <input
                  type="checkbox"
                  checked={!!state.prebreak.switches[key]}
                  onChange={(e) =>
                    onChange({
                      ...state,
                      prebreak: { ...state.prebreak, switches: { ...state.prebreak.switches, [key]: e.target.checked } },
                    })
                  }
                />
                <span className="switchtrack" />
              </span>
            </label>
          ))}
        </div>
        <div className="advsectionlabel">Coil Energy Build-up</div>
        <input
          type="range"
          min={0}
          max={COIL_STEPS.length - 1}
          step={1}
          value={coilIndex(state.prebreak.coilMin)}
          onChange={(e) =>
            onChange({ ...state, prebreak: { ...state.prebreak, coilMin: COIL_STEPS[Number(e.target.value)] } })
          }
        />
        <div className="advslidersteps">
          {COIL_STEPS.map((v, i) => (
            <span key={i}>{i === 0 ? "0(off)" : `${v}+`}</span>
          ))}
        </div>
      </FilterPopover>

      <span className="sep" />
      <button type="button" className="clearfilters" onClick={() => onChange(defaultFilterBarState())}>
        Clear filters
      </button>
      </div>
    </div>
  );
}
