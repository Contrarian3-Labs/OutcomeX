// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT} from "../src/MachineAssetNFT.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {OrderPaymentRouter} from "../src/OrderPaymentRouter.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {MockPermit2} from "../src/mocks/MockPermit2.sol";
import {MockUSDCWithAuthorization} from "../src/mocks/MockUSDCWithAuthorization.sol";
import {MockUSDT} from "../src/mocks/MockUSDT.sol";
import {OrderRecord, OrderStatus} from "../src/types/OutcomeXTypes.sol";
import {TestBase} from "./utils/TestBase.sol";

contract OrderPaymentRouterTest is TestBase {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant MACHINE_OWNER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);

    MockUSDCWithAuthorization internal usdc;
    MockUSDT internal usdt;
    MockPermit2 internal permit2;
    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;
    OrderPaymentRouter internal router;

    uint256 internal machineId;

    function setUp() public {
        usdc = new MockUSDCWithAuthorization();
        usdt = new MockUSDT();
        permit2 = new MockPermit2();
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset));
        settlement = new SettlementController(ADMIN, address(revenueVault), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));
        router = new OrderPaymentRouter(ADMIN, address(orderBook), address(usdc), address(usdt), address(pwr), address(permit2));

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        usdc.mint(ADMIN, 5_000_000);
        usdt.mint(ADMIN, 5_000_000);
        usdc.mint(BUYER, 5_000_000);
        usdt.mint(BUYER, 5_000_000);
        revenueVault.setSettlementController(address(settlement));
        router.setSettlementEscrow(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(address(router));
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(MACHINE_OWNER, "ipfs://machine-001");
        vm.stopPrank();
    }

    function testUSDCFailedBeforePreviewCanRefundRealFunds() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000_000);

        vm.prank(BUYER);
        router.payWithUSDCByAuthorization(
            orderId, 1_000_000, block.timestamp - 1, block.timestamp + 1 days, keccak256("nonce-1"), 0, bytes32(0), bytes32(0)
        );

        assertEq(usdc.balanceOf(address(settlement)), 1_000_000, "settlement should escrow usdc");

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Refunded), "expected refunded status");
        assertEq(settlement.refundableByToken(BUYER, address(usdc)), 1_000_000, "refund ledger mismatch");

        vm.prank(BUYER);
        uint256 claimed = settlement.claimRefund(address(usdc));
        assertEq(claimed, 1_000_000, "refund amount mismatch");
        assertEq(usdc.balanceOf(BUYER), 5_000_000, "buyer should recover all usdc");
        assertEq(usdc.balanceOf(address(settlement)), 0, "settlement escrow should be empty");
    }

    function testCreateAndPayWithUSDCRecordsBuyerAsCaller() public {
        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithUSDC(
            machineId, 1_000_000, block.timestamp - 1, block.timestamp + 1 days, keccak256("nonce-create-pay-1"), 0, bytes32(0), bytes32(0)
        );

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdc.balanceOf(address(settlement)), 1_000_000, "settlement should escrow usdc");
    }

    function testCreateAndPayWithUSDTRecordsBuyerAsCaller() public {
        vm.prank(BUYER);
        usdt.approve(address(permit2), 1_000_000);

        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithUSDT(machineId, 1_000_000, 1, block.timestamp + 1 days, hex"BEEF");

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdt.balanceOf(address(settlement)), 1_000_000, "settlement should escrow usdt");
    }

    function testCreateAndPayWithPWRRecordsBuyerAsCaller() public {
        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithPWR(machineId, 1_000);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(pwr.balanceOf(address(settlement)), 1_000, "settlement should escrow pwr");
    }

    function testCreatePaidOrderByAdapterBlocksTransferImmediately() public {
        uint256 adminBalanceBefore = usdc.balanceOf(ADMIN);
        vm.startPrank(ADMIN);
        usdc.approve(address(router), 1_000_000);
        uint256 orderId = router.createPaidOrderByAdapter(BUYER, machineId, 1_000_000, address(usdc));
        vm.stopPrank();

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should match adapter input");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(orderBook.activeTaskCountByMachine(machineId), 1, "active task should be tracked");
        assertEq(usdc.balanceOf(address(settlement)), 1_000_000, "settlement should escrow adapter funds");
        assertEq(usdc.balanceOf(ADMIN), adminBalanceBefore - 1_000_000, "adapter caller should fund escrow");

        (bool canTransfer, bytes32 reason) = orderBook.canTransfer(machineId, MACHINE_OWNER, address(0xDEAD));
        assertTrue(!canTransfer, "transfer should be blocked");
        assertEq(reason, orderBook.REASON_ACTIVE_TASK(), "active task reason mismatch");

        vm.startPrank(MACHINE_OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(
                bytes4(keccak256("TransferGuardBlocked(uint256,bytes32)")), machineId, orderBook.REASON_ACTIVE_TASK()
            )
        );
        machineAsset.transferFrom(MACHINE_OWNER, address(0xDEAD), machineId);
        vm.stopPrank();
    }

    function testCreatePaidOrderByAdapterRequiresEscrowApproval() public {
        vm.startPrank(ADMIN);
        vm.expectRevert(abi.encodeWithSelector(bytes4(keccak256("InsufficientAllowance(uint256,uint256)")), 0, 1_000_000));
        router.createPaidOrderByAdapter(BUYER, machineId, 1_000_000, address(usdc));
        vm.stopPrank();
    }

    function testLegacyCreateOrderThenPayWithPWRStillWorks() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        router.payWithPWR(orderId, 1_000);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "legacy path should keep original buyer");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "legacy path should mark paid");
    }

    function testUSDTConfirmedOrderCreatesRealPlatformClaimAndReserve() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000_000);

        vm.prank(BUYER);
        usdt.approve(address(permit2), 1_000_000);

        vm.prank(BUYER);
        router.payWithUSDT(orderId, 1_000_000, 1, block.timestamp + 1 days, hex"BEEF");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedByToken(address(usdt)), 100_000, "platform usdt claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 900_000, "machine pwr claim mismatch");
        assertEq(usdt.balanceOf(address(settlement)), 1_000_000, "escrow should hold full reserve before claims");

        vm.prank(PLATFORM_TREASURY);
        uint256 claimed = settlement.claimPlatformRevenue(address(usdt));
        assertEq(claimed, 100_000, "platform claim amount mismatch");
        assertEq(usdt.balanceOf(PLATFORM_TREASURY), 100_000, "platform should receive real usdt");
        assertEq(usdt.balanceOf(address(settlement)), 900_000, "remaining usdt should stay as reserve backing");
    }

    function testPWRConfirmedOrderCreatesPlatformClaimAndMachineReserve() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        router.payWithPWR(orderId, 1_000);

        assertEq(pwr.balanceOf(address(settlement)), 1_000, "settlement should escrow pwr");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedByToken(address(pwr)), 100, "platform pwr claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 900, "machine pwr accrual mismatch");
    }

    function testPWRFailedBeforePreviewCanRefundPaidPWR() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.prank(BUYER);
        router.payWithPWR(orderId, 1_000);

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        assertEq(settlement.refundableByToken(BUYER, address(pwr)), 1_000, "refund ledger mismatch");

        vm.prank(BUYER);
        uint256 claimed = settlement.claimRefund(address(pwr));
        assertEq(claimed, 1_000, "refund amount mismatch");
        assertEq(pwr.balanceOf(BUYER), 1_000, "buyer should recover all pwr");
        assertEq(pwr.balanceOf(address(settlement)), 0, "settlement escrow should be empty");
    }

    function testPaymentRouterRejectsExpiredUnpaidOrder() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, 1_000);
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), 1_000);

        vm.warp(block.timestamp + orderBook.UNPAID_ORDER_TTL() + 1);
        vm.prank(BUYER);
        vm.expectRevert(bytes("ORDER_EXPIRED"));
        router.payWithPWR(orderId, 1_000);
    }
}
