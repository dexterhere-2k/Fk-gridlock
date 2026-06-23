// Lightweight hook that fetches the historical replay timeline buckets
// for the Live-view Recharts timeline scrubber. Returns:
//   { timeline: [...buckets], loading, error, refetch }
import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api.js";

export function useReplayTimeline(hours = 24) {
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.getReplayTimeline(hours);
      setTimeline(r.buckets || []);
    } catch (e) {
      setError(e);
      setTimeline([]);
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => { refetch(); }, [refetch]);

  return { timeline, loading, error, refetch };
}

export default useReplayTimeline;
