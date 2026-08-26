import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { api } from '../api/client';

const FOOTER_TEXT = 'Research support only — not financial advice. No trading connectivity.';

function Nav({ to, label, end = false, count }: { to: string; label: string; end?: boolean; count?: number }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
      <span>{label}</span>
      {count !== undefined && count > 0 ? <span className="nav-count">{count > 99 ? '99+' : count}</span> : null}
    </NavLink>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const unread = useQuery({
    queryKey: ['alerts', 'unread-count'],
    queryFn: () => api.alerts({ unread_only: true, limit: 1 }),
    refetchInterval: 60_000,
    retry: false,
  });

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-name">VIGIL</span>
          <span className="brand-sub">equity research</span>
        </div>

        <div className="nav-section">Research</div>
        <Nav to="/" label="Opportunities" end />
        <Nav to="/alerts" label="Alerts" count={unread.data?.total} />
        <Nav to="/signals" label="Thesis Tracker" />
        <Nav to="/calendar" label="Calendar" />

        <div className="nav-section">Screens</div>
        <Nav to="/screens/deep-value" label="Deep Value" />
        <Nav to="/screens/momentum" label="Momentum" />
        <Nav to="/screens/setups" label="Setups" />

        <div className="nav-section">Holdings</div>
        <Nav to="/portfolio" label="Portfolio" />
        <Nav to="/watchlist" label="Watchlist" />

        <div className="nav-section">System</div>
        <Nav to="/backtests" label="Backtests" />
        <Nav to="/health" label="Data Health" />
        <Nav to="/admin" label="Model & Audit" />
        <Nav to="/settings" label="Settings" />
      </aside>

      <div className="main">
        <main className="page">{children}</main>
        <footer className="footer">{FOOTER_TEXT}</footer>
      </div>
    </div>
  );
}
