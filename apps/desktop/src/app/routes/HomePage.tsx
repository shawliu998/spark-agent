/* eslint-disable i18next/no-literal-string -- first-run product copy is intentionally English until the Phase 2 locale pass. */
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { BookOpen, FolderOpen, GraduationCap, Loader2, Plus, Settings2, Sparkles } from "lucide-react";
import { cn } from "@/lib/cn";
import { readOnboardingStep, saveOnboardingStep, type OnboardingStep } from "@/lib/onboarding";
import {
  createProject,
  listRecentProjects,
  openDemoProject,
  openProject,
  projectRoute,
  removeRecentProject,
  RESEARCH_TEMPLATES,
  type ProjectSummary,
  type ResearchTemplate,
} from "@/lib/projects";
import { useRuntimeStore } from "@/lib/runtime";
import { pickFolder, workspaceBase } from "@/lib/tauri";
import { toast } from "@/lib/toast";

export function HomePage() {
  const [step, setStep] = useState<OnboardingStep>(() => readOnboardingStep());
  const [recents, setRecents] = useState<ProjectSummary[]>([]);
  const [creating, setCreating] = useState(false);
  const [creationTemplate, setCreationTemplate] = useState<ResearchTemplate>("blank");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const refresh = async () => setRecents(await listRecentProjects());
  useEffect(() => {
    void refresh().catch((error) => toast.error(String(error)));
  }, []);

  const activate = async (project: ProjectSummary) => {
    setBusy(true);
    try {
      await useRuntimeStore.getState().switchWorkspace({ path: project.path });
      await refresh();
      navigate(projectRoute(project));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const openFolder = async () => {
    const folder = await pickFolder();
    if (!folder) return;
    setBusy(true);
    try {
      await activate(await openProject(folder));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const openDemo = async () => {
    setBusy(true);
    try {
      await activate(await openDemoProject());
      saveOnboardingStep("complete");
      setStep("complete");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const complete = (next: OnboardingStep) => {
    saveOnboardingStep(next);
    setStep(next);
  };

  if (step !== "complete") {
    return (
      <Onboarding
        step={step}
        busy={busy}
        onStep={complete}
        onDemo={() => void openDemo()}
        onCreate={() => { setCreationTemplate("blank"); setCreating(true); }}
        onSettings={() => navigate("/settings")}
      >
        {creating && (
          <ProjectCreator key={creationTemplate}
            initialTemplate={creationTemplate}
            onCancel={() => setCreating(false)}
            onCreated={(project) => {
              complete("complete");
              void activate(project);
            }}
          />
        )}
      </Onboarding>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="mx-auto max-w-5xl px-8 py-12">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted">Spark Agent</div>
            <h1 className="mt-2 font-serif text-3xl text-text">Research starts in a folder.</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">
              Open a local project or start one from a research template. General Research sessions and artifacts stay with that folder.
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => void openFolder()} disabled={busy} className={secondaryButton}>
              <FolderOpen size={15} /> Open Folder
            </button>
            <button onClick={() => { setCreationTemplate("blank"); setCreating(true); }} disabled={busy} className={primaryButton}>
              <Plus size={15} /> New Research Project
            </button>
          </div>
        </div>

        {creating && <ProjectCreator key={creationTemplate} initialTemplate={creationTemplate} onCancel={() => setCreating(false)} onCreated={(project) => void activate(project)} />}

        <section className="mt-11 grid gap-8 lg:grid-cols-[1.25fr_0.75fr]">
          <div>
            <SectionHeading title="Recent Projects" description="Project metadata lives in each folder; removing an entry never deletes files." />
            <div className="mt-4 overflow-hidden rounded-card border border-border bg-surface shadow-card">
              {recents.length === 0 ? (
                <div className="px-5 py-8 text-sm text-muted">No projects opened yet.</div>
              ) : recents.map((project) => (
                <div key={project.path} className="flex items-center gap-3 border-t border-border px-4 py-3 first:border-t-0">
                  <button className="min-w-0 flex-1 text-left" onClick={() => void activate(project)} disabled={busy}>
                    <div className="truncate text-sm font-medium text-text">{project.title}</div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-muted">{project.path}</div>
                  </button>
                  <button
                    className="rounded px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-text"
                    onClick={() => { void removeRecentProject(project.path).then(refresh); }}
                    aria-label={`Remove ${project.title} from recent projects`}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div>
            <SectionHeading title="Runtime" description="Live status comes from the bundled OpenCode runtime." />
            <RuntimeStatus />
            <button onClick={() => navigate("/settings")} className={cn(secondaryButton, "mt-4 w-full justify-center")}>
              <Settings2 size={15} /> Model and runtime settings
            </button>
            <button onClick={() => void openDemo()} disabled={busy} className={cn(secondaryButton, "mt-2 w-full justify-center")}>
              <GraduationCap size={15} /> Open Climate Trends Demo
            </button>
          </div>
        </section>

        <section className="mt-11">
          <SectionHeading title="Research Templates" description="Each template creates the standard folder layout and a General Research starter prompt." />
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {RESEARCH_TEMPLATES.map((template) => (
              <button key={template.id} onClick={() => { setCreationTemplate(template.id); setCreating(true); }} className="rounded-card border border-border bg-surface p-4 text-left shadow-card hover:bg-surface-2">
                <BookOpen size={16} className="text-accent" />
                <div className="mt-3 text-sm font-medium text-text">{template.title}</div>
                <div className="mt-1 text-xs leading-5 text-muted">{template.description}</div>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Onboarding({ step, busy, onStep, onDemo, onCreate, onSettings, children }: {
  step: OnboardingStep; busy: boolean; onStep: (step: OnboardingStep) => void; onDemo: () => void;
  onCreate: () => void; onSettings: () => void; children: ReactNode;
}) {
  const importLogin = useRuntimeStore((state) => state.importOpenCodeLogin);
  const handleImport = async () => {
    try {
      const imported = await importLogin();
      if (!imported) throw new Error("No OpenCode login was found to import.");
      onStep("project");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto bg-bg px-6 py-12">
      <div className="w-full max-w-2xl rounded-card border border-border bg-surface p-8 shadow-card">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-muted"><Sparkles size={14} /> Spark Agent · {step === "welcome" ? "1" : step === "model" ? "2" : "3"}/3</div>
        {step === "welcome" && <>
          <h1 className="mt-5 font-serif text-3xl text-text">Your desktop AI research workbench.</h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted">Give Spark Agent a research goal. It can work with literature, code, data, figures, and reports in a local project folder.</p>
          <div className="mt-7 flex flex-wrap gap-2">
            <button onClick={() => onStep("model")} className={primaryButton}>Set up a model</button>
            <button onClick={() => void handleImport()} className={secondaryButton}>Import OpenCode login</button>
            <button onClick={onDemo} disabled={busy} className={secondaryButton}>Open demo</button>
          </div>
        </>}
        {step === "model" && <>
          <h1 className="mt-5 font-serif text-3xl text-text">Connect a model when you are ready.</h1>
          <p className="mt-3 text-sm leading-6 text-muted">Use the existing provider connection flow for OpenAI, Anthropic, Gemini, OpenRouter, DeepSeek, or another configured OpenCode provider. Credentials remain in the OS credential manager.</p>
          <div className="mt-7 flex flex-wrap gap-2">
            <button onClick={onSettings} className={primaryButton}>Open model settings</button>
            <button onClick={() => onStep("project")} className={secondaryButton}>Continue without a model</button>
            <button onClick={onDemo} disabled={busy} className={secondaryButton}>Use the demo instead</button>
          </div>
        </>}
        {step === "project" && <>
          <h1 className="mt-5 font-serif text-3xl text-text">Create a research project.</h1>
          <p className="mt-3 text-sm leading-6 text-muted">Choose a local folder and a template. Templates add only project folders and a General Research starter prompt.</p>
          <div className="mt-7 flex flex-wrap gap-2"><button onClick={onCreate} className={primaryButton}>Create project</button><button onClick={onDemo} disabled={busy} className={secondaryButton}>Open demo</button></div>
        </>}
        {children}
      </div>
    </div>
  );
}

function ProjectCreator({ initialTemplate, onCancel, onCreated }: { initialTemplate: ResearchTemplate; onCancel: () => void; onCreated: (project: ProjectSummary) => void }) {
  const [title, setTitle] = useState("");
  const [template, setTemplate] = useState<ResearchTemplate>(initialTemplate);
  const [parent, setParent] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { void workspaceBase().then((path) => setParent((current) => current || path || "")); }, []);
  const chooseParent = async () => { const picked = await pickFolder(); if (picked) setParent(picked); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try { onCreated(await createProject(parent, title, template)); }
    catch (error) { toast.error(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  return <form onSubmit={(event) => void submit(event)} className="mt-7 rounded-card border border-border bg-bg p-5">
    <div className="grid gap-4 md:grid-cols-2">
      <label className="text-xs font-medium text-text">Project name<input required value={title} onChange={(event) => setTitle(event.target.value)} placeholder="My research project" className="mt-1.5 w-full rounded-input border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent" /></label>
      <label className="text-xs font-medium text-text">Template<select value={template} onChange={(event) => setTemplate(event.target.value as ResearchTemplate)} className="mt-1.5 w-full rounded-input border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent">{RESEARCH_TEMPLATES.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
    </div>
    <label className="mt-4 block text-xs font-medium text-text">Project location<div className="mt-1.5 flex gap-2"><input required value={parent} onChange={(event) => setParent(event.target.value)} placeholder="Choose a local folder" className="min-w-0 flex-1 rounded-input border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent" /><button type="button" onClick={() => void chooseParent()} className={secondaryButton}>Browse</button></div></label>
    <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onCancel} className={secondaryButton}>Cancel</button><button disabled={busy || !parent || !title.trim()} className={primaryButton}>{busy && <Loader2 size={14} className="animate-spin" />} Create project</button></div>
  </form>;
}

function RuntimeStatus() {
  const status = useRuntimeStore((state) => state.status);
  const model = useRuntimeStore((state) => state.defaultModel);
  const [testing, setTesting] = useState(false);
  const testConnection = async () => {
    setTesting(true);
    try {
      await useRuntimeStore.getState().connect();
      await useRuntimeStore.getState().loadCatalog();
      const selected = useRuntimeStore.getState().defaultModel;
      if (!selected) throw new Error("Runtime connected, but no default model is selected.");
      toast.success(`Model connection ready: ${selected}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setTesting(false);
    }
  };
  return <div className="mt-4 rounded-card border border-border bg-surface p-4 text-sm"><div className="flex justify-between"><span className="text-muted">Runtime</span><span className="capitalize text-text">{status}</span></div><div className="mt-2 flex justify-between gap-4"><span className="text-muted">Model</span><span className="truncate text-text">{model ?? "Not connected"}</span></div><button onClick={() => void testConnection()} disabled={testing} className={cn(secondaryButton, "mt-4 w-full justify-center")}>{testing && <Loader2 size={14} className="animate-spin" />} Test model connection</button></div>;
}

function SectionHeading({ title, description }: { title: string; description: string }) { return <div><h2 className="font-serif text-xl text-text">{title}</h2><p className="mt-1 text-sm text-muted">{description}</p></div>; }

const primaryButton = "inline-flex items-center justify-center gap-1.5 rounded-input bg-accent px-3 py-2 text-sm font-medium text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton = "inline-flex items-center justify-center gap-1.5 rounded-input border border-border bg-surface px-3 py-2 text-sm text-text hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50";
