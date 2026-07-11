// Every type below is a thin alias onto `./schema.ts` — the file
// `openapi-typescript` generates from `src/trades/api.py`'s own OpenAPI
// schema (see `scripts/export_openapi_schema.py` and the
// `generate:schema` npm script). Nothing here is hand-typed against the
// JSON shape anymore: a backend field rename shows up here automatically
// next time `schema.ts` is regenerated, and CI's `openapi-types` workflow
// fails the build if it isn't.
import type { components } from './schema'

export type Overview = components['schemas']['Overview']
export type DollarChartPoint = components['schemas']['DollarChartPoint']
export type ReallocationMarker = components['schemas']['ReallocationMarker']
export type DollarChart = components['schemas']['DollarChart']
export type GrowthOf100Point = components['schemas']['GrowthOf100Point']
export type CashHistoryPoint = components['schemas']['CashHistoryPoint']
export type CashSitting = components['schemas']['CashSitting']
export type CashSittingWarningLevel = CashSitting['warning_level']
export type MonthlyPnl = components['schemas']['MonthlyPnlRow']
export type MonthlyPnlBySymbol = components['schemas']['MonthlyPnlBySymbolRow']
export type AllocationRow = components['schemas']['AllocationRow']

// `GET`/`PUT /api/settings/target-allocation` deliberately return a bare
// `symbol -> target percentage` map with no fixed keys (see
// `trades.api.get_target_allocation`) — there's no named response model
// for openapi-typescript to generate a component for, so this one stays
// hand-written.
export type TargetAllocation = Record<string, number>

export type OpenLot = components['schemas']['OpenLotRow']
export type ClosedLot = components['schemas']['ClosedLotRow']
export type SymbolRollup = components['schemas']['SymbolRollupRow']
export type LotsTable = components['schemas']['LotsTable']
export type RiskStat = components['schemas']['RiskStat']
export type DataQualityRow = components['schemas']['DataQualityRow']
export type LedgerEvent = components['schemas']['LedgerEvent']
export type SyncStep = components['schemas']['SyncStep']
export type SyncResult = components['schemas']['SyncResult']
export type HysaBank = components['schemas']['HysaBank']
export type HysaRatePoint = components['schemas']['HysaRatePoint']
export type HysaRates = components['schemas']['HysaRates']
export type HysaSettings = components['schemas']['HysaSettings']
export type BenchmarkSetting = components['schemas']['BenchmarkSetting']
export type BenchmarkSettingUpdate = components['schemas']['BenchmarkSettingUpdate']
export type IbkrSettings = components['schemas']['IbkrSettings']
export type IbkrSettingsUpdate = components['schemas']['IbkrCredentialsUpdate']
export type VerifyResult = components['schemas']['trades__api__api_models__VerifyResult']
export type SymbolSearchResult = components['schemas']['SymbolSearchResult']
export type SymbolPriceStatus = components['schemas']['SymbolPriceStatus']
export type SyncProgress = components['schemas']['SyncProgress']
export type TaxSettings = components['schemas']['TaxSettings']
export type TaxRegime = TaxSettings['resolved_tax_regime']
export type TaxSettingsUpdate = components['schemas']['TaxSettingsUpdate']
export type AnnualTaxRow = components['schemas']['AnnualTaxRow']
export type WashSaleRow = components['schemas']['WashSaleRow']
export type SalePreviewRow = components['schemas']['SalePreviewRow']
export type TaxOwedRow = components['schemas']['TaxOwedRow']
export type TaxReport = components['schemas']['TaxReport']
