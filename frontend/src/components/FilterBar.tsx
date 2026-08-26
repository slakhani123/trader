import type { ReactNode } from 'react';

/** Horizontal filter strip; compose with the field helpers below. */
export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="filter-bar">{children}</div>;
}

export function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="filter-field">
      <span className="flabel">{label}</span>
      {children}
    </label>
  );
}

export function FilterSelect({
  label,
  value,
  onChange,
  options,
  anyLabel = 'Any',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  anyLabel?: string;
}) {
  return (
    <FilterField label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </FilterField>
  );
}

export function FilterNumber({
  label,
  value,
  onChange,
  min = 0,
  max = 10,
  step = 0.5,
  placeholder,
}: {
  label: string;
  value: number | '';
  onChange: (v: number | '') => void;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
}) {
  return (
    <FilterField label={label}>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
      />
    </FilterField>
  );
}

export function FilterToggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function FilterText({
  label,
  value,
  onChange,
  placeholder,
  width = 130,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  width?: number;
}) {
  return (
    <FilterField label={label}>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        style={{ width }}
        onChange={(e) => onChange(e.target.value)}
      />
    </FilterField>
  );
}
