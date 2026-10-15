// k6: many users like the same post at a fixed arrival rate (Phase 6).
//
// Each iteration likes the hot post with a different user, so every request is a real
// new like (no idempotent no-ops). tokens.json comes from `make loadtest-setup`.
//
//   k6 run -e API=http://localhost:8000 -e RATE=400 -e DURATION=20s loadtest/hot_post.js
import http from "k6/http";
import exec from "k6/execution";
import { check } from "k6";

const data = JSON.parse(open("./tokens.json"));
const API = __ENV.API || "http://localhost:8000";
const RATE = Number(__ENV.RATE || 300);
const DURATION = __ENV.DURATION || "20s";

export const options = {
  scenarios: {
    hot_post: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 200,
      maxVUs: 1000,
    },
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

export default function () {
  const i = exec.scenario.iterationInTest;
  if (i >= data.tokens.length) {
    exec.test.abort(`ran out of users: raise --users above ${data.tokens.length}`);
  }
  const res = http.post(`${API}/posts/${data.post_id}/like`, null, {
    headers: { Authorization: `Bearer ${data.tokens[i]}` },
  });
  check(res, { "status 200": (r) => r.status === 200 });
}
