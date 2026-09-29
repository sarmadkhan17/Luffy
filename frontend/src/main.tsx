/** Entry. The production build is LIVE: `import.meta.env.MODE === "demo"` is a
 * build-time constant, so the DEMO branch and its fixtures are not emitted. */
import { mountLive } from "./liveRoot";
export { routes } from "./Shell";
if (import.meta.env.MODE === "demo") void import("./demo").then((m) => m.mountDemo());
else mountLive();
