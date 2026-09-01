import os


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
        """Reads markdown text files from all project folders to provide full context."""
        projects = self.get_projects()
        all_context = []
        for project in projects:
            all_context.append(f"=== PROJECT: {project} ===")
            all_context.append(self.get_project_context(project))
        return "\n\n".join(all_context)
