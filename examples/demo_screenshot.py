import time
import logging
from rich.console import Console
from rich.panel import Panel

# Suppress internal python logging so only our beautiful rich text shows
logging.getLogger("stateguard").setLevel(logging.CRITICAL)

from stateguard.proxy import GuardedState
from stateguard.saga import Saga, saga_step
from stateguard.invariants import guard, InvariantViolation

console = Console()

# 1. Define our strict memory invariant
@guard.invariant(name="valid_hotel_booking")
def check_hotel_days(state):
    # Rule: You cannot book a hotel for negative days
    if "hotel_days" in state:
        return state["hotel_days"] > 0
    return True

# 2. Define a saga compensation (Undo logic)
@saga_step(compensate=lambda *args, result=None, **kwargs: console.print(f"🔄 [bold yellow]Executing Saga Compensation:[/bold yellow] Canceling {result['flight']}... Refunded!"))
def book_flight(state, destination):
    console.print(f"🛫 [bold blue]Agent Action:[/bold blue] Successfully booked flight to {destination}.")
    state["flight"] = destination
    return {"flight": destination}

def simulate_llm_hallucination(state):
    # The LLM randomly outputs corrupted JSON/Data
    state["hotel_days"] = -5
    state["action"] = "book_hotel"

def run_demo():
    console.print(Panel.fit("[bold cyan]StateGuard Live Demo[/bold cyan]\nExecuting LangGraph Travel Agent..."))
    
    # Standard Python Dictionary Memory
    memory = {"user": "shankar", "balance": 1000}
    state = GuardedState(memory)
    
    time.sleep(1)
    
    try:
        # Open a Transactional Saga
        with Saga(state, mode="block") as tx:
            
            # Step 1: Agent works normally
            console.print("⏳ [gray]Agent 1 thinking...[/gray]")
            time.sleep(1.5)
            book_flight(state, "Tokyo")
            
            # Step 2: Agent hallucinates
            console.print("\n⏳ [gray]Agent 2 processing hotel...[/gray]")
            time.sleep(2)
            
            console.print("🧠 [bold magenta]LLM Output:[/bold magenta] [italic]{'hotel_days': -5, 'action': 'book_hotel'}[/italic]")
            simulate_llm_hallucination(state)
            
            console.print("\n⏳ [gray]Committing state to database...[/gray]")
            time.sleep(1)

    except InvariantViolation as e:
        console.print("\n🚨 [bold red]STATEGUARD INTERCEPTION![/bold red]")
        console.print(f"❌ [red]Invariant Violation:[/red] {e.message}")
        console.print("🛡️  [red]Blocking state save and triggering rollback...[/red]\n")
        time.sleep(1.5)
        
    console.print(Panel.fit(
        f"[bold green]✅ Transaction Rolled Back Successfully[/bold green]\n"
        f"Memory restored to pristine state: {dict(state)}",
        border_style="green"
    ))

if __name__ == "__main__":
    run_demo()
