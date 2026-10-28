"""LangGraph orchestration; durable ownership/replay lives in the SQL Store.

No secret credentials in state, no automatic node retries, no external tracing,
no unreviewed file tool execution. The model callback must reserve before I/O.
"""
from typing import TypedDict,Any
from langgraph.graph import StateGraph,START,END
from langgraph.types import Command
from langsmith import tracing_context


class TurnState(TypedDict,total=False):
    use_model:bool
    response:dict[str,Any]


def build_turn_graph(retrieve,model,persist,*,checkpointer=None):
    def search_node(state):
        return {'response':retrieve()}
    def model_node(state):
        return {'response':model(state['response'])}
    def persist_node(state):
        return {'response':persist(state['response'])}
    graph=StateGraph(TurnState)
    graph.add_node('owner_scoped_tool',search_node,retry_policy=None)
    graph.add_node('reserved_model_call',model_node,retry_policy=None)
    graph.add_node('persist_readonly_turn',persist_node,retry_policy=None)
    graph.add_edge(START,'owner_scoped_tool')
    graph.add_conditional_edges('owner_scoped_tool',lambda state:'model' if state['use_model'] else 'readonly',{'model':'reserved_model_call','readonly':'persist_readonly_turn'})
    graph.add_edge('reserved_model_call',END)
    graph.add_edge('persist_readonly_turn',END)
    return graph.compile(checkpointer=checkpointer)


def run_turn_graph(retrieve,model,persist,use_model,*,checkpointer=None,thread_id=None,resume=False):
    """Run once by default; explicit resume=True retries pending tasks.

    Pass a LangGraph Command(resume=...) to resume an interrupt. The caller
    must bind a trusted thread and authorize resumption; HTTP turns do not
    enable this path automatically.
    """
    config={'recursion_limit':8}
    if checkpointer is not None:
        if not isinstance(thread_id,str) or not thread_id:
            raise ValueError('A server-owned thread_id is required with a checkpointer')
        config['configurable']={'thread_id':thread_id}
    elif resume is not False:
        raise ValueError('Resumption requires a checkpointer')
    if resume is False:
        inputs={'use_model':use_model}
    elif resume is True:
        inputs=None
    elif isinstance(resume,Command):
        inputs=resume
    else:
        raise ValueError('resume must be a boolean or LangGraph Command')
    with tracing_context(enabled=False):
        result=build_turn_graph(retrieve,model,persist,checkpointer=checkpointer).invoke(inputs,config=config)
    if '__interrupt__' in result:
        return {'__interrupt__':result['__interrupt__'],'orchestrator':'langgraph'}
    return {**result['response'],'orchestrator':'langgraph'}
