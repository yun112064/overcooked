export type PackageUnit = 'g' | 'kg' | 'mL' | 'L'

export function packageDimension(unit: PackageUnit) {
  return unit === 'g' || unit === 'kg' ? 'mass' : 'volume'
}

/** Shift the decimal point without rounding package evidence through a float. */
function shiftDecimal(value: string, places: number): string | null {
  const parts = /^(\d{1,19})(?:\.(\d{0,9}))?$/.exec(value.trim())
  if (!parts) return null
  const fraction = parts[2] ?? ''
  const digits = parts[1] + fraction
  const point = parts[1].length + places
  const integer = (point <= 0 ? '0' : digits.slice(0, point).padEnd(point, '0')).replace(/^0+(?=\d)/, '')
  const decimals = (point < 0 ? '0'.repeat(-point) + digits : digits.slice(Math.max(0, point))).replace(/0+$/, '')
  return integer + (decimals ? '.' + decimals : '')
}

export function canonicalPackageSize(value: string, unit: PackageUnit): string | null {
  const canonical = shiftDecimal(value, unit === 'kg' || unit === 'L' ? 3 : 0)
  if (!canonical || canonical === '0' || !/^\d{1,16}(?:\.\d{1,6})?$/.test(canonical)) return null
  return canonical
}

export function displayPackageSize(value: string | null, unit: PackageUnit): string {
  if (value === null) return ''
  return shiftDecimal(value, unit === 'kg' || unit === 'L' ? -3 : 0) ?? value
}
