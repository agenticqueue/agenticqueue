import http from "node:http";

const port = Number(process.env.AQ_E2E_AUTH_API_PORT ?? "3127");

const state = {
  needs_bootstrap: false,
};

function sendJson(response, status, payload, headers = {}) {
  response.writeHead(status, {
    "content-type": "application/json",
    ...headers,
  });
  response.end(JSON.stringify(payload));
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host}`);

  if (request.method === "GET" && url.pathname === "/v1/health") {
    sendJson(response, 200, { status: "ok" });
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/auth/bootstrap_status") {
    sendJson(response, 200, { needs_bootstrap: state.needs_bootstrap });
    return;
  }

  if (request.method === "POST" && url.pathname === "/__aq_e2e/state") {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      const payload = JSON.parse(body || "{}");
      state.needs_bootstrap = Boolean(payload.needs_bootstrap);
      sendJson(response, 200, state);
    });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/session") {
    sendJson(
      response,
      200,
      {
        user: { email: "admin@example.com", is_admin: true },
      },
      { "set-cookie": "aq_session=playwright; Path=/; HttpOnly; SameSite=Lax" },
    );
    return;
  }

  sendJson(response, 404, { error: "Not found" });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`AQ e2e auth API listening on http://127.0.0.1:${port}`);
});
