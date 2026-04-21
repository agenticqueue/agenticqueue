import { NextRequest, NextResponse } from "next/server";

import {
  buildDecisionLineage,
  loadDecisionDataset,
  UpstreamError,
} from "../../data";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ id: string }> },
) {
  const authorization = request.headers.get("authorization")?.trim();
  if (!authorization) {
    return NextResponse.json(
      { error: "Authorization header is required." },
      { status: 401 },
    );
  }

  try {
    const { id } = await context.params;
    const dataset = await loadDecisionDataset({
      authorization,
      signal: request.signal,
    });
    const lineage = buildDecisionLineage(id, dataset);

    if (!lineage) {
      return NextResponse.json(
        { error: "Decision lineage was not found." },
        { status: 404 },
      );
    }

    return NextResponse.json(lineage);
  } catch (error: unknown) {
    if (error instanceof UpstreamError) {
      return NextResponse.json(
        { error: error.message, status: error.status },
        { status: error.status },
      );
    }

    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to load decision lineage.",
      },
      { status: 500 },
    );
  }
}
