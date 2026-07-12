export const FILTER_ALL = '__all__'

// A filter value paired with an "Is"/"Not" toggle — the "show everything
// but this one" mode every equality filter in this app shares. `undefined`/
// unset values from a filter state persisted before this field existed are
// treated as "no filter", never as "matches nothing".
export function matchesFilter(actual: boolean, filterValue: string | undefined, exclude: boolean | undefined): boolean {
  if (!filterValue || filterValue === FILTER_ALL) return true
  return exclude ? !actual : actual
}
