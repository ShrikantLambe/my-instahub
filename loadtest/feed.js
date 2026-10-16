// k6: signed-in users opening their home feed (Phase 10, fan-out on read vs write).
// feed_tokens.json comes from scripts/feedtest.py.
//
//   k6 run -e API=http://localhost:8000 -e RATE=200 -e DURATION=20s loadtest/feed.js
import http from "k6/http";
import { check } from "k6";

const tokens = JSON.parse(open("./feed_tokens.json")).tokens;
const API = __ENV.API || "http://localhost:8000";

export const options = {
  scenarios: {
    feed: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 200),
      timeUnit: "1s",
      duration: __ENV.DURATION || "20s",
      preAllocatedVUs: 200,
      maxVUs: 1000,
    },
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

export default function () {
  const token = tokens[Math.floor(Math.random() * tokens.length)];
  const res = http.get(`${API}/feed?limit=10`, { headers: { Authorization: `Bearer ${token}` } });
  check(res, { "status 200": (r) => r.status === 200 && r.json("posts").length > 0 });
}
