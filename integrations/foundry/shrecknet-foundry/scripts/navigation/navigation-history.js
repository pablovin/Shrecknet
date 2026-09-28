/** Lightweight in-app history, deliberately independent from browser history. */
export class NavigationHistory {
  constructor(initial) {
    this.current = initial;
    this.backStack = [];
    this.forwardStack = [];
  }

  navigate(next) {
    if (this.current) this.backStack.push(this.current);
    this.current = next;
    this.forwardStack = [];
    return this.current;
  }

  back() {
    if (!this.backStack.length) return this.current;
    this.forwardStack.push(this.current);
    this.current = this.backStack.pop();
    return this.current;
  }

  forward() {
    if (!this.forwardStack.length) return this.current;
    this.backStack.push(this.current);
    this.current = this.forwardStack.pop();
    return this.current;
  }

  home(home) {
    return this.navigate(home);
  }

  get canGoBack() { return this.backStack.length > 0; }
  get canGoForward() { return this.forwardStack.length > 0; }
}
