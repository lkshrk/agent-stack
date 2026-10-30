# shellcheck shell=sh
# Login shells (Hermes' terminal tool runs bash -l) get PATH reset by /etc/profile; put the agent tools back.
case ":$PATH:" in
  *:/opt/agent/bin:*) ;;
  *) PATH="/opt/agent/bin:$PATH" ;;
esac
export PATH
