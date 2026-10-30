"""Headless timing tests, without camera or GUI dependencies."""
import ast
import unittest
from pathlib import Path
source=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
nodes=[n for n in source.body if isinstance(n,ast.ClassDef) and n.name in ('Trigger','Monitor')]
namespace={}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app_core','exec'),namespace)
Monitor=namespace['Monitor']

class TimingTests(unittest.TestCase):
    def setUp(self): self.m=Monitor()
    def state(self,t,score=.8,closed=False,face=True,inv=False):
        return self.m.update(score,closed,face,.55,.35,1.0,t,inv)[0]
    def test_gaze_delay_and_immediate_stop(self):
        self.assertFalse(self.state(0))
        self.assertFalse(self.state(.34))
        self.assertTrue(self.state(.35))
        self.assertFalse(self.state(.36,score=.2))
    def test_closed_delay_and_reopening(self):
        self.assertFalse(self.state(0,closed=True,score=None))
        self.assertFalse(self.state(.99,closed=True,score=None))
        self.assertTrue(self.state(1,closed=True,score=None))
        self.assertFalse(self.state(1.1,score=.2))
    def test_blink_does_not_trigger(self):
        self.assertFalse(self.state(0,closed=True))
        self.assertFalse(self.state(.2,score=.2))
        self.assertFalse(self.state(.3,closed=True))
        self.assertFalse(self.state(1.2,closed=True))
    def test_face_loss_cancels_closed_timer(self):
        self.state(0,closed=True)
        self.assertFalse(self.state(.8,face=False))
        self.assertFalse(self.state(1.2,closed=True))
        self.assertTrue(self.state(2.3,closed=True))
        self.assertFalse(self.state(2.4,face=False))
    def test_transitions_have_separate_timers(self):
        self.state(0)
        self.assertTrue(self.state(.4))
        self.assertFalse(self.state(.5,closed=True))
        self.assertFalse(self.state(1.49,closed=True))
        self.assertTrue(self.state(1.5,closed=True))
        self.assertFalse(self.state(1.6))
        self.assertTrue(self.state(2))
    def test_inverted_direction(self):
        self.assertFalse(self.state(0,score=.2,inv=True))
        self.assertTrue(self.state(.35,score=.2,inv=True))
        self.assertFalse(self.state(.4,score=.8,inv=True))
    def test_untracked_eyes_are_not_closed(self):
        self.assertFalse(self.state(0,score=None))
        self.assertFalse(self.state(2,score=None))

if __name__=='__main__': unittest.main()
