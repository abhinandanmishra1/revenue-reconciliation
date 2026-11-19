import { NextResponse, type NextRequest } from "next/server";

const AUTH_PAGES = ["/login", "/signup"];

/** UX only: the API enforces every access rule; this just avoids flashing protected pages. */
export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession = request.cookies.has("session");

  if (AUTH_PAGES.includes(pathname)) {
    const expired = request.nextUrl.searchParams.has("expired");
    return hasSession && !expired ? NextResponse.redirect(new URL("/imports", request.url)) : NextResponse.next();
  }
  if (!hasSession) {
    const url = new URL("/login", request.url);
    url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/|_next/|favicon.ico).*)"],
};
