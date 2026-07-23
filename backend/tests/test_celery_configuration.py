import unittest

from app.celery_app import celery_app


class CeleryConfigurationTests(unittest.TestCase):
    """验证 RabbitMQ 可靠投递和死信队列的静态配置。"""

    def test_publisher_confirm_is_enabled(self) -> None:
        self.assertTrue(celery_app.conf.broker_transport_options["confirm_publish"])

    def test_late_ack_and_worker_loss_requeue_are_enabled(self) -> None:
        self.assertTrue(celery_app.conf.task_acks_late)
        self.assertTrue(celery_app.conf.task_reject_on_worker_lost)
        self.assertFalse(celery_app.conf.task_acks_on_failure_or_timeout)

    def test_main_queue_routes_rejected_messages_to_dead_letter_queue(self) -> None:
        queues = {queue.name: queue for queue in celery_app.conf.task_queues}
        main_queue = queues[celery_app.conf.task_default_queue]

        self.assertEqual(
            main_queue.queue_arguments["x-dead-letter-exchange"],
            "ai_knowledge_hub.dlx",
        )
        self.assertEqual(
            main_queue.queue_arguments["x-dead-letter-routing-key"],
            "dead",
        )
        self.assertIn("ai_knowledge_hub.dead", queues)


if __name__ == "__main__":
    unittest.main()
