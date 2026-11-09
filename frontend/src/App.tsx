import { NavLink, Route, Routes } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useLiveUpdates } from "./api/live";
import { api } from "./api/client";
import { keys, useApiMutation, useTools } from "./api/queries";
import { Badge, EmptyState, LinkButton } from "./ui/kit";
import { AssetsPage } from "./pages/AssetsPage";
import { NewAssetPage } from "./pages/NewAssetPage";
import { AssetPage } from "./pages/AssetPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { ProfileEditPage } from "./pages/ProfileEditPage";
import { LorasPage } from "./pages/LorasPage";
import { OutfitsPage } from "./pages/OutfitsPage";
import { SetupPage } from "./pages/SetupPage";
import { SkillsPage } from "./pages/SkillsPage";
import { SkillTestPage } from "./pages/SkillTestPage";
import { SetsPage } from "./pages/SetsPage";
import { SetPage } from "./pages/SetPage";

const NAV = [
  { to: "/", label: "Assets", end: true },
  { to: "/projects", label: "Projects" },
  { to: "/profiles", label: "Style profiles" },
  { to: "/outfits", label: "Outfits" },
  { to: "/skills", label: "Skills" },
  { to: "/skill-test", label: "Skill test" },
  { to: "/sets", label: "Character sets" },
  { to: "/loras", label: "LoRA library" },
  { to: "/setup", label: "Setup" },
];

/** One badge per external tool: green when it answers, red when it does not (or is not built yet). */
function ToolHealth() {
  const tools = useTools();
  const restart = useApiMutation((tool: string) => api.restartTool(tool), [keys.tools]);
  return (
    <>
      {(tools.data ?? []).map((t) => {
        const badge = (
          <Badge tone={t.ok ? "success" : "danger"} dot pulse={restart.isPending && restart.variables === t.tool}>
            {t.name}
            {t.ok ? "" : restart.isPending && restart.variables === t.tool ? " restarting…" : t.detail.includes("not built yet") ? " not built" : " unreachable"}
          </Badge>
        );
        return !t.ok && t.restartable ? (
          <button key={t.tool} type="button" className="badge-button" title={`${t.detail}. Click to restart it.`}
                  disabled={restart.isPending} onClick={() => restart.mutate(t.tool)}>
            {badge}
          </button>
        ) : (
          <NavLink key={t.tool} to="/setup" className="badge-button" title={`${t.detail}. Open Setup to restart or stop it.`}>
            {badge}
          </NavLink>
        );
      })}
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
          Forge
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
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/skill-test" element={<SkillTestPage />} />
          <Route path="/sets" element={<SetsPage />} />
          <Route path="/sets/:id" element={<SetPage />} />
          <Route path="/setup" element={<SetupPage />} />
          <Route
            path="*"
            element={<EmptyState title="Page not found" action={<LinkButton to="/">Back to assets</LinkButton>} />}
          />
        </Routes>
      </main>
    </div>
  );
}
