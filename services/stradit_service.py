import os

# StradIT's service lines and company-level facts, from https://www.stradit.com/.
# Unlike the per-project docs under StradIT/<PROJECT>/, these aren't tied to a
# local markdown file - kept as a static reference here so project-matching
# (see StoryAgent.generate_channel_storyline) can select a genuine service
# engagement, not just one of the three named software products, when that's
# what a competitor post actually connects to.
SERVICES_CONTEXT = """=== SERVICE: Applied Artificial Intelligence ===
Production-grade AI workflows with guardrails, human oversight, and clear ROI paths.
Capabilities: intelligent process automation, LLM governance and guardrails, AI readiness
training for teams, responsible AI playbooks.

=== SERVICE: Data Analytics (Applied AI) ===
Turns fragmented data into trusted insights; built for speed, accuracy, and action.
Capabilities: modern analytics foundations, AI-powered data quality, executive dashboards,
predictive models.

=== SERVICE: Cyber Security (Applied AI) ===
Strengthens security posture with AI-driven threat intelligence.
Capabilities: security architecture, AI-enhanced threat visibility, compliance & audit
readiness, secure AI & data protection.

=== SERVICE: Cloud & Infrastructure (Applied AI) ===
AI-optimized cloud & infrastructure that's resilient, scalable, and cost-aware.
Capabilities: AI-assisted cloud migration, infrastructure modernization, reliability
engineering, platform standardization.

=== SERVICE: Automated AI Testing ===
Ships faster with AI-powered testing woven into every release.
Capabilities: automation-first QA, performance & resilience testing, test strategy and
tooling, continuous quality systems.

=== SERVICE: Digital Assets & Blockchain ===
Blockchain-native infrastructure for regulated on-chain capital markets.
Capabilities: distributed ledger infrastructure, smart contract automation, token issuance
& custody, SEC / FCA / MiCA compliance.

=== SERVICE: Global Capability Center (GCC) ===
AI-enabled centers that operate as a true extension of a client's business.
Capabilities: GCC setup and operating model, talent/tooling/delivery governance, Center of
Excellence (CoE) design and scaling, continuous performance improvement.

=== COMPANY OVERVIEW ===
StradIT serves Capital Markets, Asset Management, Banking (Tier-1 global institutions),
Trading, Settlement, and Regulatory Reporting (RegTech). Operates across USA, UK, Europe,
and Asia, with hubs in Hudson Yards (New York) and London/Canary Wharf (EMEA)."""


class StradITService:
    def __init__(self):
        self.base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "StradIT")

    def get_projects(self):
        """Returns a list of available StradIT projects."""
        if not os.path.exists(self.base_dir):
            return []
        return [d for d in os.listdir(self.base_dir) if os.path.isdir(os.path.join(self.base_dir, d))]

    def get_project_context(self, project_name):
        """Reads markdown text files from the project folder to provide context."""
        project_dir = os.path.join(self.base_dir, project_name)
        if not os.path.exists(project_dir):
            return f"Project {project_name} not found."

        context = []
        for filename in os.listdir(project_dir):
            if filename.endswith(".md") or filename.endswith(".txt"):
                filepath = os.path.join(project_dir, filename)
                try:
                    with open(filepath, encoding="utf-8") as f:
                        context.append(f"--- Document: {filename} ---\n{f.read()}")
                except Exception as e:
                    print(f"Error reading {filename}: {e}")

        return "\n\n".join(context)

    def get_all_projects_context(self):
        """Reads markdown text files from all project folders, plus StradIT's
        service lines and company overview, to provide the full context used
        for project/service matching.

        SERVICES_CONTEXT is placed FIRST, not appended after the project docs:
        those docs alone run 50K-180K+ characters each, and testing showed a
        short section appended after ~285K characters of project docs was
        effectively invisible to the model ("lost in the middle") - it kept
        returning "No Strong Match" for posts that clearly fit a listed
        service, but matched correctly the moment the same section led the
        context instead. Project docs are still much larger overall, so they
        remain the dominant content; this only protects the compact services
        section from being drowned out entirely.
        """
        projects = self.get_projects()
        all_context = [SERVICES_CONTEXT]
        for project in projects:
            all_context.append(f"=== PROJECT: {project} ===")
            all_context.append(self.get_project_context(project))
        return "\n\n".join(all_context)
