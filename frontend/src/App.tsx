import { ActiveIncident, Analytics, ClusterHealth, LiveFeed } from "./components";
import { useLiveData } from "./useLiveData";

export default function App() {
  const { connected, jobs, eventsIngested, feed, summary } = useLiveData();
  return (
    <>
      <header>
        <h1>
          The <span>best</span> GPU anomaly monitoring system <span>ever</span>
        </h1>
        <div className="stat">
          <span className={`conn ${connected ? "up" : ""}`} />
          <b>{eventsIngested.toLocaleString()}</b> telemetry events ingested
        </div>
      </header>
      <div className="wrap">
        <div>
          <ClusterHealth jobs={jobs} />
          <ActiveIncident jobs={jobs} />
        </div>
        <div>
          <LiveFeed feed={feed} />
          <Analytics summary={summary} />
        </div>
      </div>
    </>
  );
}
