const app = { name: "FlowCode Studio", stack: ["HTML", "CSS", "JavaScript", "Python", "Eel"] };

function boot() {
  console.log(`Welcome to ${app.name}`);
  console.table(app.stack);
}

boot();
