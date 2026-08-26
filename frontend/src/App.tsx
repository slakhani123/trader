import { Navigate, Route, Routes } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Layout } from './components/Layout';
import { AdminPage } from './pages/Admin';
import { AlertDetailPage } from './pages/AlertDetail';
import { AlertsPage } from './pages/Alerts';
import { BacktestDetailPage } from './pages/BacktestDetail';
import { BacktestsPage } from './pages/Backtests';
import { CalendarPage } from './pages/Calendar';
import { CompanyPage } from './pages/Company';
import { HealthPage } from './pages/Health';
import { OpportunitiesPage } from './pages/Opportunities';
import { PortfolioPage } from './pages/Portfolio';
import { ScreenPage } from './pages/Screen';
import { SettingsPage } from './pages/Settings';
import { SignalsPage } from './pages/Signals';
import { WatchlistPage } from './pages/Watchlist';

export function App() {
  return (
    <Layout>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<OpportunitiesPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/alerts/:id" element={<AlertDetailPage />} />
          <Route path="/companies/:id" element={<CompanyPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route
            path="/screens/deep-value"
            element={
              <ScreenPage
                key="deep-value"
                title="Deep Value"
                description="Cheap on multiple measures with a margin of safety — deep_value family."
                families={['deep_value']}
                defaultHorizon="long"
              />
            }
          />
          <Route
            path="/screens/momentum"
            element={
              <ScreenPage
                key="momentum"
                title="Momentum"
                description="Breakout continuation, estimate revisions and fundamental inflections."
                families={['breakout_continuation', 'estimate_momentum', 'fundamental_inflection']}
                defaultHorizon="medium"
              />
            }
          />
          <Route
            path="/screens/setups"
            element={
              <ScreenPage
                key="setups"
                title="Setups"
                description="Oversold-at-support and constructive pullback entries."
                families={['oversold_at_support', 'constructive_pullback']}
                defaultHorizon="short"
              />
            }
          />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/signals" element={<SignalsPage />} />
          <Route path="/backtests" element={<BacktestsPage />} />
          <Route path="/backtests/:id" element={<BacktestDetailPage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ErrorBoundary>
    </Layout>
  );
}
