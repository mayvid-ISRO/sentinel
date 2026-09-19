class Tool:
    def __init__(self, name, description, args_schema, func):
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self.func = func

    def run(self, args):
        return self.func(**args)