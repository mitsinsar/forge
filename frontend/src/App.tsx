import { NavLink, Route, Routes } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useLiveUpdates } from "./api/live";
import { useTools } from "./api/queries";
import { Badge, EmptyState, LinkButton } from "./ui/kit";
import { AssetsPage } from "./pages/AssetsPage";
import { NewAssetPage } from "./pages/NewAssetPage";
import { AssetPage } from "./pages/AssetPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { ProfileEditPage } from "./pages/ProfileEditPage";
import { LorasPage } from "./pages/LorasPage";
import { OutfitsPage } from "./pages/OutfitsPage";

const NAV = [
  { to: "/", label: "Assets", end: true },
  { to: "/projects", label: "Projects" },
  { to: "/profiles", label: "Style profiles" },
  { to: "/outfits", label: "Outfits" },
  { to: "/loras", label: "LoRA library" },
];

/** One badge per external tool: green when it answers, red when it does not (or is not built yet). */
function ToolHealth() {
  const tools = useTools();
  return (
    <>
      {(tools.data ?? []).map((t) => (
        <span key={t.tool} title={t.detail}>
          <Badge tone={t.ok ? "success" : "danger"} dot>
            {t.name}
            {t.ok ? "" : t.detail.includes("not built yet") ? " not built" : " unreachable"}
          </Badge>
        </span>
      ))}
    </>
  );
}

export function App() {
  const live = useLiveUpdates(useQueryClient());
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="topbar__brand">
          <img src="/favicon.svg" alt="" />
          Forge of Figures
        </NavLink>
        <nav className="topbar__nav">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className="topbar__link">
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar__status">
          <ToolHealth />
          <span title="Live updates from the server">
            {live === "live" ? (
              <Badge tone="success" dot>
                Live
              </Badge>
            ) : (
              <Badge tone={live === "offline" ? "danger" : "neutral"} dot pulse>
                {live === "offline" ? "Offline" : "Connecting"}
              </Badge>
            )}
          </span>
          <LinkButton to="/new" variant="primary" size="sm">
            New asset
          </LinkButton>
        </div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<AssetsPage />} />
          <Route path="/new" element={<NewAssetPage />} />
          <Route path="/asset/:id" element={<AssetPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="/profiles/new" element={<ProfileEditPage />} />
          <Route path="/profiles/:id" element={<ProfileEditPage />} />
          <Route path="/loras" element={<LorasPage />} />
          <Route path="/outfits" element={<OutfitsPage />} />
          <Route
            path="*"
            element={<EmptyState title="Page not found" action={<LinkButton to="/">Back to assets</LinkButton>} />}
          />
        </Routes>
      </main>
    </div>
  );
}
