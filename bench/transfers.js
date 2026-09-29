// k6 load test for POST /v1/transfers.
//
//   SCENARIO=spread  every virtual user moves money between its own pair of
//                    accounts, so no two requests contend for a row lock.
//   SCENARIO=hot     every virtual user pays into one shared account, so
//                    every request queues on that account's lock.
//
// See bench/README.md for how to run it and what the numbers mean.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const TOKEN = __ENV.TOKEN;
const SCENARIO = __ENV.SCENARIO || "spread";
const VUS = Number(__ENV.VUS || 20);

export const options = {
  scenarios: {
    transfers: {
      executor: "constant-vus",
      vus: VUS,
      duration: __ENV.DURATION || "30s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

function post(path, body) {
  return http.post(`${BASE}${path}`, JSON.stringify(body), {
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    tags: { name: path },
  });
}

function openFundedAccount(amount) {
  const account = post("/v1/accounts", { owner_id: "bench", currency: "USD" }).json();
  if (amount) {
    post("/v1/deposits", { account_id: account.id, amount, currency: "USD" });
  }
  return account.id;
}

export function setup() {
  if (!TOKEN) {
    throw new Error("set TOKEN to a client token (manage.py create_api_client bench)");
  }
  const hot = openFundedAccount(null);
  const pairs = [];
  for (let i = 0; i < VUS; i++) {
    pairs.push([openFundedAccount("1000000000"), openFundedAccount(null)]);
  }
  return { hot, pairs };
}

export default function (data) {
  const [source, own] = data.pairs[(__VU - 1) % data.pairs.length];
  const destination = SCENARIO === "hot" ? data.hot : own;
  const response = post("/v1/transfers", {
    from: source,
    to: destination,
    amount: "1.00",
    currency: "USD",
  });
  check(response, { "201 Created": (r) => r.status === 201 });
}
